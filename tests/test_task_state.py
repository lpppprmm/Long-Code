"""Recovery tests for independent task records and evidence-backed handoffs."""

import copy
import json
import tempfile
import unittest
from pathlib import Path
from threading import Event
from unittest.mock import patch

from anthropic.types import Message
from fastapi.testclient import TestClient

from agent import Agent
from api import create_app
from session import (
    Registry,
    bootstrap_session,
    load_session,
    prepare_context,
    read_json,
    save_state,
)
from task_state import (
    load_task,
    record_request,
    source_record,
    task_context,
    task_document,
    task_path,
    update_task,
)
from tools import RunCancelled, execute_tool, read_file, write_file


def response(calls=()):
    return Message.model_validate({
        "id": "offline", "type": "message", "role": "assistant", "model": "offline",
        "content": [{"type": "tool_use", "id": f"call-{i}", "name": name, "input": args}
                    for i, (name, args) in enumerate(calls)] or [{"type": "text", "text": "Done"}],
        "stop_reason": "tool_use" if calls else "end_turn", "stop_sequence": None,
        "usage": {"input_tokens": 10, "output_tokens": 10},
    })


class OfflineModel:
    def __init__(self, steps):
        self.messages = self
        self.steps = iter(steps)

    def create(self, **kwargs):
        step = next(self.steps)
        return step(kwargs) if callable(step) else step


class TaskStateTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        self.repo = self.base / "repo"
        self.repo.mkdir()
        self.registry = Registry(self.base / "data")
        self.addCleanup(self.registry.close)
        self.project = self.registry.create("Example", str(self.repo))
        self.session = load_session(self.project)

    def start(self, request="Fix the parser. Preserve the public API."):
        self.session.active_request = request
        source = record_request(self.project, self.session, request, new_task=True)
        save_state(self.project, self.session)
        return source

    def tool(self, name, args, **kwargs):
        return execute_tool(self.project, self.session,
                            {"id": "test-call", "name": name, "input": args}, **kwargs)

    def evidence(self):
        task = load_task(self.project, self.session)
        return task["recent_evidence"][-1]["id"]

    def note(self, key="public-api", text="Preserve the public API.", kind="constraint",
             status="active", evidence=None):
        return {"id": key, "text": text, "kind": kind, "status": status,
                "evidence": ["request-0001"] if evidence is None else evidence}

    def action(self, evidence=()):
        return {"action": "Inspect the empty-input branch in parser.py.",
                "verification": "Run the parser regression test, then the parser suite.",
                "files": ["parser.py"], "evidence": list(evidence)}

    def test_notes_and_original_sources_survive_five_lossy_rollovers(self):
        self.start()
        self.tool("bash", {"command": "printf 'empty input fails'; exit 7"})
        source = self.evidence()
        update_task(self.project, self.session, notes=[
            self.note(), self.note("failed-fix", "Changing the delimiter did not fix empty input.",
                                   "failed_attempt", evidence=[source]),
            self.note("cache", "The cache may be involved.", "finding", "unverified", []),
        ], next_action=self.action([source]))
        task_id = self.session.task_id
        original = task_path(self.project, task_id, "request-0001.json").read_bytes()
        expected_notes = copy.deepcopy(load_task(self.project, self.session)["notes"])
        bootstrap_session(self.project, self.session)
        for number in range(1, 6):
            for transition in ("compact", "rollover"):
                self.assertEqual(prepare_context(
                    self.project, self.session, lambda *_: "Lossy summary without task details.",
                    force=True,
                ), transition)
                text = json.dumps(self.session.messages)
                for expected in ("Preserve the public API", "delimiter did not fix", "unverified", "empty-input branch"):
                    self.assertIn(expected, text)
            self.session = load_session(self.project)
            bootstrap_session(self.project, self.session)
            self.assertEqual(self.session.task_id, task_id)
            self.assertEqual(load_task(self.project, self.session)["notes"], expected_notes)
            checkpoint = read_json(self.project.checkpoint_file(number))
            self.assertEqual(checkpoint["task"]["notes"], expected_notes)
        self.assertEqual(self.session.id, 6)
        self.assertEqual(task_path(self.project, task_id, "request-0001.json").read_bytes(), original)
        evidence = source_record(self.project, load_task(self.project, self.session), source)
        self.assertEqual(evidence["execution"]["exit_code"], 7)
        self.assertIn("empty input fails", evidence["output"])

    def test_followup_supersedes_constraint_without_rewriting_original(self):
        self.start()
        update_task(self.project, self.session, notes=[self.note()])
        path = task_path(self.project, self.session.task_id, "request-0001.json")
        original = path.read_bytes()
        record_request(self.project, self.session, "You may change the public API now.")
        update_task(self.project, self.session, notes=[
            self.note(status="superseded", evidence=["request-0002"]),
            self.note("api-change", "Public API changes are now allowed.", evidence=["request-0002"]),
        ])
        context = task_context(self.project, self.session)
        self.assertNotIn("[public-api]", context)
        self.assertIn("[api-change]", context)
        self.assertEqual(path.read_bytes(), original)
        task = load_task(self.project, self.session)
        self.assertEqual(task["notes"]["public-api"]["status"], "superseded")

    def test_evidence_detects_file_changes_without_git(self):
        self.start()
        self.tool("write_file", {"path": "parser.py", "content": "value = 1\n"})
        source = self.evidence()
        update_task(self.project, self.session, next_action=self.action([source]))
        self.assertNotIn("Evidence needs rechecking", task_context(self.project, self.session, verify=True))
        (self.repo / "parser.py").write_text("value = 2\n")
        bootstrap_session(self.project, self.session)
        self.assertIn("Evidence needs rechecking", self.session.messages[0]["content"])
        self.assertIn("parser.py", self.session.messages[0]["content"])
        task_path(self.project, self.session.task_id, f"{source}.json").unlink()
        self.assertIn("evidence unavailable", task_context(self.project, self.session, verify=True))

    def test_note_updates_are_incremental_and_failed_writes_are_atomic(self):
        self.start()
        update_task(self.project, self.session, notes=[self.note()], next_action=self.action())
        update_task(self.project, self.session, notes=[self.note("unknown", "Check caching.", "finding", "unverified", [])])
        task = load_task(self.project, self.session)
        self.assertEqual(set(task["notes"]), {"public-api", "unknown"})
        self.assertEqual(task["next_action"], self.action())
        before = task_path(self.project, self.session.task_id).read_bytes()
        with patch("session.os.replace", side_effect=OSError("disk full")), self.assertRaises(OSError):
            update_task(self.project, self.session, next_action={**self.action(), "action": "Try a different fix."})
        self.assertEqual(task_path(self.project, self.session.task_id).read_bytes(), before)

    def test_invalid_updates_do_not_partially_change_notes(self):
        self.start()
        update_task(self.project, self.session, notes=[self.note()])
        before = load_task(self.project, self.session)
        for note in (
            self.note("bad", evidence=["../state.json"]),
            self.note("bad", evidence=["evidence-" + "a" * 32]),
            self.note("bad", evidence=[]),
            self.note("bad", kind="finding"),
            self.note("public-api", kind="decision"),
        ):
            with self.subTest(note=note), self.assertRaises(ValueError):
                update_task(self.project, self.session, notes=[self.note("would-add"), note])
            self.assertEqual(load_task(self.project, self.session), before)
        with self.assertRaises(ValueError):
            update_task(self.project, self.session, notes=[self.note("would-add")],
                        next_action={**self.action(), "files": ["../escape"]})
        self.assertEqual(load_task(self.project, self.session), before)

    def test_evidence_and_notes_are_task_and_project_scoped(self):
        self.start()
        self.tool("read_file", {"path": "missing.py"})
        old_task = load_task(self.project, self.session)
        source = self.evidence()
        self.start("A different task")
        with self.assertRaisesRegex(ValueError, "Unknown task evidence"):
            update_task(self.project, self.session, notes=[self.note("finding", "Missing file.", "finding", evidence=[source])])
        self.assertEqual(source_record(self.project, old_task, source)["status"], "failed")
        other_root = self.base / "other"
        other_root.mkdir()
        other = self.registry.create("Other", str(other_root))
        with self.assertRaises(ValueError):
            source_record(other, old_task, source)
        self.assertEqual(task_context(other, load_session(other)), "")

    def test_bounded_preview_keeps_full_records_and_requires_reading_omitted_notes(self):
        self.start("Original request " * 2000)
        for i in range(24):
            update_task(self.project, self.session, notes=[self.note(f"rule-{i}", f"Rule {i}: " + "x" * 500)])
        context = task_context(self.project, self.session, limit=3000)
        self.assertLessEqual(len(context), 3000)
        self.assertIn("Required before editing", context)
        self.assertEqual(len(load_task(self.project, self.session)["notes"]), 24)
        request = source_record(self.project, load_task(self.project, self.session), "request-0001")
        self.assertEqual(request["text"], "Original request " * 2000)
        with self.assertRaises(ValueError):
            update_task(self.project, self.session, notes=[self.note("one-too-many")])

    def test_long_outputs_and_exit_status_are_retrievable_from_evidence(self):
        self.start()
        result = self.tool("bash", {"command": "printf '%012000d' 0"}, output_limit=500)
        self.assertLessEqual(len(result["content"]), 500)
        source = self.evidence()
        self.assertIn(source, result["content"])
        record = source_record(self.project, load_task(self.project, self.session), source)
        self.assertEqual(record["execution"]["exit_code"], 0)
        self.assertIn("agent://tool-results/", record["output"])
        output = next((self.project.data_dir / "tool-results").glob("*.txt"))
        self.assertEqual(len(output.read_text()), 12000)
        source_path = f"agent://tasks/{self.session.task_id}/{source}.json"
        self.assertIn("exit_code", read_file(self.project, source_path))
        for path in (source_path, f"agent://tasks/{self.session.task_id}/task.json"):
            with self.assertRaises(ValueError):
                write_file(self.project, path, "overwrite")

    def test_cancelled_command_preserves_interrupted_evidence(self):
        self.start()
        cancel = Event()
        cancel.set()
        with self.assertRaises(RunCancelled):
            self.tool("bash", {"command": "sleep 3"}, cancel_event=cancel)
        record = source_record(self.project, load_task(self.project, self.session), self.evidence())
        self.assertEqual(record["status"], "interrupted")
        self.assertTrue(record["execution"]["cancelled"])
        self.assertEqual(load_session(self.project).task_id, self.session.task_id)

    def test_agent_records_requests_and_archives_completed_tasks(self):
        model = OfflineModel([
            response([("task_update", {"notes": [self.note()], "next_action": self.action()})]),
            response(), response(),
        ])
        agent = Agent(model, "offline", emit=lambda _: None)
        request = "Fix parser; preserve public API."
        agent.run(self.project, self.session, request)
        old_id = self.session.task_id
        previous = load_task(self.project, self.session)
        self.assertIn("public-api", previous["notes"])
        self.assertEqual(source_record(self.project, previous, "request-0001")["text"], request)
        agent.run(self.project, self.session, "Now write documentation.")
        self.assertNotEqual(self.session.task_id, old_id)
        self.assertEqual(load_task(self.project, self.session)["notes"], {})
        self.assertEqual(read_json(task_path(self.project, old_id)), previous)

    def test_legacy_unfinished_request_is_migrated_on_continue(self):
        self.session.active_request = "Legacy unfinished request"
        save_state(self.project, self.session)
        state = read_json(self.project.state_file)
        del state["task_id"]
        self.project.state_file.write_text(json.dumps(state))
        self.session = load_session(self.project)
        agent = Agent(OfflineModel([response()]), "offline", emit=lambda _: None)
        agent.run(self.project, self.session)
        task = load_task(self.project, self.session)
        self.assertEqual(source_record(self.project, task, "request-0001")["text"], "Legacy unfinished request")
        self.assertEqual(load_session(self.project).task_id, task["id"])

    def test_api_exposes_task_view_from_the_same_record(self):
        model = OfflineModel([
            response([("task_update", {"notes": [self.note()], "next_action": self.action()})]), response(),
        ])
        app = create_app(self.registry.home, Agent(model, "offline", emit=lambda _: None))
        with TestClient(app) as client:
            client.post(f"/api/projects/{self.project.id}/open")
            result = client.post("/api/chat", json={"project_id": self.project.id, "message": "Preserve the public API."})
            self.assertEqual(result.status_code, 200, result.text)
            state = client.get("/api/state").json()
            self.assertIn("Preserve the public API", state["documents"]["task"])
            self.assertIn("empty-input branch", state["documents"]["task"])
            self.assertEqual(state["documents"]["task"], task_document(self.project, app.state.workspace.runtime.session))


if __name__ == "__main__":
    unittest.main()
