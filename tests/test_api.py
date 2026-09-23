"""HTTP integration tests: one runtime, isolated projects, and persistent UI history."""

import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event
from unittest.mock import patch

from fastapi.testclient import TestClient

from api import create_app
from session import add_message, save_state


class TestAgent:
    model = "offline-test-model"
    context_limit = 50_000
    client = None

    def __init__(self):
        self.emit = lambda _: None
        self.started = Event()
        self.release = Event()
        self.release.set()
        self.failure = False

    def get_client(self):
        return self

    def run(self, project, session, request=None):
        session.active_request = request or session.active_request
        save_state(project, session)
        add_message(project, session, "user", session.active_request)
        self.emit("[tool] read_file")
        self.started.set()
        if not self.release.wait(timeout=5):
            raise RuntimeError("Test request did not finish")
        if self.failure:
            raise RuntimeError("Model temporarily unavailable")
        self.emit("The **project** is ready.\n\n```python\nprint('hello')\n```")
        session.todos = [{"content": "Inspect project", "status": "completed"}]
        session.active_request = ""
        save_state(project, session)


class APITest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        self.repo = self.base / "repo"
        self.repo.mkdir()
        self.agent = TestAgent()
        self.app = create_app(self.base / "data", self.agent)
        self.client = TestClient(self.app)
        self.addCleanup(self.client.close)

    def create_project(self, name="First", root=None):
        response = self.client.post("/api/projects", json={"name": name, "root": str(root or self.repo)})
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    def test_initial_state_and_api_docs(self):
        state = self.client.get("/api/state").json()
        self.assertIsNone(state["project"])
        self.assertFalse(state["busy"])
        self.assertEqual(self.client.get("/api/projects").json(), [])
        self.assertEqual(self.client.get("/docs").status_code, 200)
        self.assertEqual(self.client.get("/").status_code, 404)

    def test_project_creation_and_input_validation(self):
        state = self.create_project()
        self.assertEqual(state["project"]["root"], str(self.repo))
        self.assertEqual(state["session"]["id"], 1)
        self.assertEqual(state["session"]["context_limit_tokens"], 12_500)
        self.assertEqual(state["session"]["context_tokens"], (state["session"]["context_size"] + 3) // 4)
        self.assertIn("# Project", state["documents"]["project"])
        self.assertEqual(len(self.client.get("/api/projects").json()), 1)
        for body in ({"name": "   ", "root": str(self.repo)}, {"name": "No root"}):
            self.assertEqual(self.client.post("/api/projects", json=body).status_code, 422)
        self.assertEqual(self.client.post("/api/projects", json={"name": "Other", "root": "/missing-folder"}).status_code, 400)
        self.assertFalse(self.client.get("/api/state").json()["busy"])

    def test_chat_persists_ui_history_without_replaying_model_context(self):
        self.create_project()
        response = self.client.post("/api/chat", json={"project_id": "first", "message": "Inspect this repository"})
        self.assertEqual(response.status_code, 200, response.text)
        state = response.json()
        self.assertEqual([e["kind"] for e in state["events"]], ["user", "activity", "assistant"])
        self.assertEqual(state["session"]["todos"][0]["status"], "completed")
        self.assertFalse(state["busy"])
        restored_app = create_app(self.base / "data", TestAgent())
        with TestClient(restored_app) as restored:
            response = restored.post("/api/projects/first/open")
            self.assertEqual(response.json()["events"], state["events"])
            self.assertEqual(len(restored_app.state.workspace.runtime.session.messages), 1)

    def test_event_log_is_compacted(self):
        self.create_project()
        workspace = self.app.state.workspace
        with (patch("api.MAX_EVENT_LOG_BYTES", 1000),
              patch("api.COMPACT_EVENT_LOG_BYTES", 500)):
            for number in range(30):
                workspace.record("activity", f"Event {number}: " + "x" * 100)
        path = workspace.runtime.project.data_dir / "web-events.jsonl"
        self.assertLessEqual(path.stat().st_size, 1000)
        self.assertLess(len(path.read_text().splitlines()), 30)
        self.assertEqual(workspace.events[-1]["text"], "Event 29: " + "x" * 100)

    def test_switching_clears_ui_history_and_rejects_stale_chat(self):
        self.create_project()
        self.client.post("/api/chat", json={"project_id": "first", "message": "FIRST_PRIVATE_MARKER"})
        second = self.base / "second"
        second.mkdir()
        state = self.create_project("Second", second)
        self.assertEqual(state["events"], [])
        self.assertEqual(self.client.post("/api/chat", json={"project_id": "first", "message": "Wrong project"}).status_code, 409)
        self.assertEqual(self.client.post("/api/projects/missing/open").status_code, 404)
        self.assertEqual(self.client.get("/api/state").json()["project"]["id"], "second")
        first = self.client.post("/api/projects/first/open").json()
        self.assertEqual(first["events"][0]["text"], "FIRST_PRIVATE_MARKER")

    def test_run_errors_keep_request_and_release_lock(self):
        self.create_project()
        self.agent.failure = True
        response = self.client.post("/api/chat", json={"project_id": "first", "message": "Finish the request"})
        self.assertEqual(response.status_code, 502)
        state = self.client.get("/api/state").json()
        self.assertFalse(state["busy"])
        self.assertEqual(state["session"]["active_request"], "Finish the request")
        self.assertEqual(state["events"][-1]["kind"], "error")
        self.agent.failure = False
        self.assertEqual(self.client.post("/api/chat", json={"project_id": "first"}).status_code, 200)
        self.assertEqual(self.client.post("/api/chat", json={"project_id": "first"}).status_code, 400)
        self.assertEqual(self.client.post("/api/chat", json={"project_id": "first", "message": "  "}).status_code, 422)

    def test_busy_state_remains_readable_and_blocks_mutations(self):
        self.create_project()
        self.agent.release.clear()
        with ThreadPoolExecutor(max_workers=1) as pool:
            run = pool.submit(self.client.post, "/api/chat", json={"project_id": "first", "message": "Work"})
            try:
                self.assertTrue(self.agent.started.wait(timeout=3))
                state = self.client.get("/api/state").json()
                self.assertTrue(state["busy"])
                self.assertEqual(state["events"][-1]["kind"], "activity")
                self.assertEqual(self.client.post("/api/projects/first/open").status_code, 409)
                self.assertEqual(self.client.post("/api/chat", json={"project_id": "first", "message": "Again"}).status_code, 409)
                self.assertEqual(self.client.post("/api/projects", json={"name": "Again", "root": str(self.repo)}).status_code, 409)
            finally:
                self.agent.release.set()
            self.assertEqual(run.result(timeout=3).status_code, 200)

    def test_history_search_is_project_scoped(self):
        self.create_project()
        self.client.post("/api/chat", json={"project_id": "first", "message": "FIRST_PRIVATE_MARKER"})
        second = self.base / "second"
        second.mkdir()
        self.create_project("Second", second)
        first = self.client.get("/api/projects/first/history", params={"query": "FIRST_PRIVATE_MARKER"}).json()
        other = self.client.get("/api/projects/second/history", params={"query": "FIRST_PRIVATE_MARKER"}).json()
        self.assertIn("FIRST_PRIVATE_MARKER", first["text"])
        self.assertEqual(other["text"], "No matching history.")

    def test_cors_allows_only_configured_frontend_origins(self):
        allowed = self.client.options("/api/chat", headers={"Origin": "http://127.0.0.1:5173", "Access-Control-Request-Method": "POST", "Access-Control-Request-Headers": "content-type"})
        self.assertEqual(allowed.status_code, 200)
        self.assertEqual(allowed.headers["access-control-allow-origin"], "http://127.0.0.1:5173")
        rejected = self.client.options("/api/chat", headers={"Origin": "https://unrelated.example", "Access-Control-Request-Method": "POST"})
        self.assertEqual(rejected.status_code, 400)
        self.assertNotIn("access-control-allow-origin", rejected.headers)

    def test_simple_cross_origin_post_cannot_switch_projects(self):
        self.create_project()
        response = self.client.post("/api/projects/first/open", headers={"Origin": "https://unrelated.example"})
        self.assertEqual(response.status_code, 403)
        self.assertEqual(self.client.get("/api/state").json()["project"]["id"], "first")

    def test_unknown_host_is_rejected(self):
        self.assertEqual(self.client.get("/api/state", headers={"Host": "unrelated.example"}).status_code, 400)


if __name__ == "__main__":
    unittest.main()
