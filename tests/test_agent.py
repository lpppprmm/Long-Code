"""Offline integration tests for the documented V1 lifecycle and boundaries."""

import copy
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from threading import Event, Thread
from types import SimpleNamespace
from unittest.mock import patch

from agent import Agent
from main import Application
from session import (
    HANDOFF_SECTIONS,
    Registry,
    Session,
    add_message,
    atomic_write,
    bootstrap_session,
    configured_context_limit,
    estimate_context,
    estimate_tokens,
    inspect_project,
    load_session,
    prepare_context,
    read_json,
    rollover_session,
    save_state,
    search_history,
    verify_checkpoint,
)
from tools import (
    TOOLS,
    edit_file,
    execute_tool,
    glob,
    read_file,
    run_bash,
    todo_write,
    write_file,
)


def summary(prompt, data):
    if "handoff" in prompt:
        return "# Session Handoff\n\n" + "\n\n".join(
            f"## {section}\n\nContinue the implementation and verify files." for section in HANDOFF_SECTIONS
        )
    return "The implementation is partly complete. Keep changes concise and run tests."


class Block:
    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)

    def model_dump(self, **kwargs):
        return self.__dict__.copy()


def response(text="Done.", calls=(), stop=None):
    content = [Block(type="text", text=text)] if text else []
    content += [Block(type="tool_use", id=f"call_{i}", name=name, input=args)
                for i, (name, args) in enumerate(calls)]
    return SimpleNamespace(content=content, stop_reason=stop or ("tool_use" if calls else "end_turn"))


class FakeClient:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.messages = self
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(copy.deepcopy(kwargs))
        item = next(self.responses)
        if isinstance(item, BaseException):
            raise item
        return item


class ContextBudgetTest(unittest.TestCase):
    def test_configuration_and_legacy_precedence(self):
        for env, characters, tokens in (
            ({}, 400_000, 100_000),
            ({"CONTEXT_LIMIT_TOKENS": "20000"}, 80_000, 20_000),
            ({"CONTEXT_LIMIT": "50000"}, 50_000, 12_500),
            ({"CONTEXT_LIMIT_TOKENS": "100000", "CONTEXT_LIMIT": "50000"}, 400_000, 100_000),
        ):
            with self.subTest(env=env), patch.dict(os.environ, env, clear=True):
                agent = Agent(context_limit=configured_context_limit())
                self.assertEqual(agent.context_limit, characters)
                self.assertEqual(estimate_tokens(agent.context_limit), tokens)

    def test_invalid_token_budget(self):
        for value in ("", "invalid", "1.5", "0", "-1", "499"):
            with (self.subTest(value=value), patch.dict(os.environ, CONTEXT_LIMIT_TOKENS=value),
                  self.assertRaises(ValueError)):
                Agent(context_limit=configured_context_limit())

    def test_token_estimate_rounds_up(self):
        self.assertEqual([estimate_tokens(n) for n in (0, 1, 4, 5)], [0, 1, 1, 2])


class ProjectTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.repo = self.base / "repo-a"
        self.repo.mkdir()
        (self.repo / "README.md").write_text("Actual project requirements", encoding="utf-8")
        self.registry = Registry(self.base / "data")
        self.project = self.registry.create("Project A", str(self.repo))
        self.session = load_session(self.project)
        bootstrap_session(self.project, self.session)

    def other_project(self):
        root = self.base / "repo-b"
        root.mkdir()
        return self.registry.create("Project B", str(root))

    def grow(self):
        add_message(self.project, self.session, "user", "Old detailed investigation: " + "x" * 9000)

    def test_new_project_and_registry(self):
        self.assertEqual((self.session.id, self.session.compact_count), (1, 0))
        self.assertFalse(self.project.handoff_file.exists())
        self.assertEqual(set(self.registry.entries()[self.project.id]), {"name", "root"})
        self.assertIn("Actual project requirements", self.session.messages[0]["content"])
        self.assertEqual(read_json(self.project.state_file)["current_session"], 1)
        with self.assertRaises(ValueError):
            self.registry.create("Duplicate path", str(self.repo))
        with self.assertRaises(ValueError):
            self.registry.open("../../outside")
        with self.assertRaises(ValueError):
            self.registry.create("Missing", str(self.base / "missing"))

    def test_failed_registry_write_cleans_new_project_and_allows_retry(self):
        root = self.base / "new-repo"
        root.mkdir()
        with (patch("session.write_json", side_effect=OSError("registry write failed")),
              self.assertRaises(OSError)):
            self.registry.create("Retry Project", str(root))
        self.assertFalse((self.registry.home / "projects" / "retry-project").exists())
        self.assertNotIn("retry-project", self.registry.entries())
        self.assertEqual(self.registry.create("Retry Project", str(root)).id, "retry-project")

    def test_unicode_project_name_gets_a_safe_id(self):
        root = self.base / "unicode-repo"
        root.mkdir()
        project = self.registry.create("中文项目", str(root))
        self.assertTrue(project.id.startswith("project-"))
        self.assertEqual(self.registry.open(project.id).name, "中文项目")

    def test_data_home_rejects_a_second_process(self):
        script = ("import sys; from pathlib import Path; "
                  "from session import Registry; Registry(Path(sys.argv[1]))")
        command = [sys.executable, "-c", script, str(self.registry.home)]
        blocked = subprocess.run(command, cwd=Path(__file__).resolve().parents[1],
                                 capture_output=True, text=True, check=False)
        self.assertNotEqual(blocked.returncode, 0)
        self.assertIn("already in use", blocked.stderr)
        self.registry.close()
        reopened = subprocess.run(command, cwd=Path(__file__).resolve().parents[1],
                                  capture_output=True, text=True, check=False)
        self.assertEqual(reopened.returncode, 0, reopened.stderr)

    def test_compact_rollover_and_repeat(self):
        self.session.active_request = "Finish the parser with no extra dependency"
        original_message = {"role": "user", "content": "unique original detail"}
        add_message(self.project, self.session, **original_message)
        for expected in (1, 2):
            self.grow()
            self.assertEqual(prepare_context(self.project, self.session, summary, 8000), "compact")
            self.assertEqual((self.session.id, self.session.compact_count), (expected, 1))
            self.assertLess(estimate_context(self.session.messages), 8000)
            self.grow()
            old_messages = self.session.messages
            self.assertEqual(prepare_context(self.project, self.session, summary, 8000), "rollover")
            self.assertEqual((self.session.id, self.session.compact_count), (expected + 1, 0))
            self.assertIsNot(self.session.messages, old_messages)
            self.assertEqual(len(self.session.messages), 1)
            self.assertNotIn("Old detailed investigation", self.session.messages[0]["content"])
            self.assertIn("Finish the parser", self.session.messages[0]["content"])
            handoff = self.project.handoffs_dir / f"session_{expected:03d}.md"
            self.assertEqual(handoff.read_text(), self.project.handoff_file.read_text())
        records = [json.loads(line) for line in self.project.transcript_file(1).read_text().splitlines()]
        self.assertIn(original_message, records)
        self.assertEqual(sum(r.get("event") == "soft_compact" for r in records), 1)

    def test_compaction_preserves_tool_pairs(self):
        add_message(self.project, self.session, "assistant", [
            {"type": "tool_use", "id": "c1", "name": "glob", "input": {"pattern": "*"}}
        ])
        add_message(self.project, self.session, "user", [
            {"type": "tool_result", "tool_use_id": "c1", "content": "README.md"}
        ])
        add_message(self.project, self.session, "assistant", [{"type": "text", "text": "Found it"}])
        add_message(self.project, self.session, "user", "Continue")
        add_message(self.project, self.session, "assistant", [{"type": "text", "text": "Inspecting"}])
        prepare_context(self.project, self.session, summary, force=True)
        self.assertEqual(self.session.messages[1]["content"][0]["id"], "c1")
        self.assertEqual(self.session.messages[2]["content"][0]["tool_use_id"], "c1")

    def test_restart_keeps_state_without_loading_transcripts(self):
        self.session.id, self.session.compact_count = 8, 1
        self.session.active_request = "Finish outstanding changes"
        save_state(self.project, self.session)
        add_message(self.project, self.session, "user", "PRIVATE OLD MESSAGES")
        reopened = load_session(self.registry.open(self.project.id))
        self.assertEqual((reopened.id, reopened.compact_count), (8, 1))
        self.assertEqual(reopened.active_request, "Finish outstanding changes")
        self.assertEqual(reopened.messages, [])
        bootstrap_session(self.project, reopened)
        self.assertNotIn("PRIVATE OLD MESSAGES", json.dumps(reopened.messages))

    def test_project_switching_isolates_messages_todos_and_history(self):
        other = self.other_project()
        app = Application(self.registry.home)
        app.open(self.project.id)
        app.session.id = 8
        app.session.active_request = "A unfinished request"
        add_message(self.project, app.session, "user", "A_PRIVATE_MARKER")
        todo_write(self.project, app.session, [{"content": "A todo", "status": "pending"}])
        old = app.session
        app.open(other.id)
        self.assertEqual(old.messages, [])
        self.assertEqual(old.todos, [])
        self.assertEqual((app.session.id, app.session.todos), (1, []))
        self.assertNotIn("A_PRIVATE_MARKER", json.dumps(app.session.messages))
        self.assertEqual(search_history(other, "A_PRIVATE_MARKER"), "No matching history.")
        self.assertIn("A_PRIVATE_MARKER", search_history(self.project, "A_PRIVATE_MARKER"))
        app.open(self.project.id)
        self.assertEqual(app.session.id, 8)
        self.assertEqual(app.session.todos[0]["content"], "A todo")

    def test_wrong_handoff_does_not_replace_inspection(self):
        self.project.handoff_file.write_text("## Current State\nmissing.py exists and tests passed")
        (self.repo / "actual.py").write_text("assert True\n")
        bootstrap_session(self.project, self.session)
        text = self.session.messages[0]["content"]
        self.assertIn("actual.py", text)
        self.assertIn("missing.py exists", text)
        self.assertIn("it may be wrong", text)
        self.assertLess(text.index("Directory entries"), text.index("missing.py exists"))
        self.assertFalse((self.repo / "missing.py").exists())

    def test_git_inspection_is_scoped_even_inside_parent_repository(self):
        subprocess.run(["git", "init", "-q", str(self.base)], check=True)
        outside = self.base / "other-project-secret.txt"
        outside.write_text("Other project contents")
        subprocess.run(["git", "-C", str(self.base), "add", "repo-a/README.md", outside.name], check=True)
        inspection = inspect_project(self.project)
        self.assertIn("README.md", inspection)
        self.assertNotIn(outside.name, inspection)
        self.assertIn(f"Git root: {self.base} (ancestor repository)", inspection)

    def test_git_inspection_identifies_local_and_missing_repositories(self):
        self.assertIn("no project-local repository detected", inspect_project(self.project))
        subprocess.run(["git", "init", "-q", str(self.repo)], check=True)
        self.assertIn(f"Git root: {self.repo} (project-local)", inspect_project(self.project))

    def test_summary_receives_full_history_before_shrinking(self):
        add_message(self.project, self.session, "user", "prefix" * 5000)
        add_message(self.project, self.session, "user", "IMPORTANT_MIDDLE_DECISION")
        add_message(self.project, self.session, "user", "suffix" * 5000)

        def summarize_all(prompt, data):
            self.assertIn("IMPORTANT_MIDDLE_DECISION", data)
            return summary(prompt, data)

        prepare_context(self.project, self.session, summarize_all, 8000)

    def test_handoff_failure_preserves_session(self):
        before = copy.deepcopy(self.session)
        with self.assertRaisesRegex(RuntimeError, "service unavailable"):
            rollover_session(self.project, self.session,
                             lambda *_: (_ for _ in ()).throw(RuntimeError("service unavailable")))
        self.assertEqual(self.session, before)
        self.assertFalse(self.project.handoff_file.exists())
        self.assertEqual(load_session(self.project).id, 1)

    def test_rollover_recovers_interrupted_latest_handoff_write(self):
        self.session.active_request = "Finish recovery"
        original = atomic_write

        def fail_latest(path, text):
            if path == self.project.handoff_file:
                raise OSError("disk failure")
            original(path, text)

        with (patch("session.atomic_write", side_effect=fail_latest),
              self.assertRaisesRegex(OSError, "disk failure")):
            rollover_session(self.project, self.session, summary)
        reopened = load_session(self.project)
        self.assertEqual(reopened.id, 2)
        self.assertEqual(reopened.active_request, "Finish recovery")
        self.assertTrue(self.project.handoff_file.exists())

    def test_handoff_retains_unfinished_todos(self):
        todo_write(self.project, self.session, [{"content": "Verify parser", "status": "pending"}])
        rollover_session(self.project, self.session, summary)
        self.assertIn("Verify parser", self.project.handoff_file.read_text())
        self.assertEqual(self.session.todos, [{"content": "Verify parser", "status": "pending"}])
        self.assertEqual(load_session(self.project).todos, self.session.todos)
        checkpoint = read_json(self.project.checkpoint_file(1))
        self.assertEqual(checkpoint["unfinished_todos"], self.session.todos)

    def test_checkpoint_detects_file_changes_after_rollover(self):
        subprocess.run(["git", "init", str(self.repo)], check=True, capture_output=True)
        write_file(self.project, "parser.py", "value = 1\n")
        self.session.changed_files = ["parser.py"]
        rollover_session(self.project, self.session, summary)
        checkpoint = read_json(self.project.checkpoint_file(1))
        self.assertTrue(checkpoint["repository"]["available"])
        self.assertIn("match", verify_checkpoint(self.project, checkpoint))
        write_file(self.project, "parser.py", "value = 2\n")
        self.assertIn("file_hashes", verify_checkpoint(self.project, checkpoint))
        bootstrap_session(self.project, self.session)
        self.assertIn("Repository changed since the handoff", self.session.messages[0]["content"])
        self.assertIn('"status": "changed"', self.project.transcript_file(2).read_text())

    def test_model_usage_is_recorded_and_guides_context_threshold(self):
        agent = Agent(model="fake", context_limit=8000, max_tokens=100)
        usage = SimpleNamespace(usage=SimpleNamespace(input_tokens=1700, output_tokens=20))
        agent.record_usage(self.project, self.session, usage, "agent", estimate_context(self.session.messages))
        self.assertEqual((self.session.last_input_tokens, self.session.total_output_tokens), (1700, 20))
        self.assertFalse(agent.context_needs_prepare(self.session))
        add_message(self.project, self.session, "user", "x" * 1000)
        self.assertTrue(agent.context_needs_prepare(self.session))
        self.assertLess(estimate_context(self.session.messages), agent.context_limit)
        self.assertEqual(load_session(self.project).last_input_tokens, 1700)
        self.assertIn('"event": "model_usage"', self.project.transcript_file(1).read_text())
        agent.client = FakeClient([response("Measured compaction summary")])
        self.assertEqual(agent.prepare(self.project, self.session), "compact")
        self.assertEqual(self.session.compact_count, 1)

    def test_atomic_write_failure_keeps_previous_json(self):
        before = self.project.state_file.read_text()
        with (patch("session.os.replace", side_effect=OSError("disk failure")),
              self.assertRaises(OSError)):
            save_state(self.project, Session(id=99))
        self.assertEqual(self.project.state_file.read_text(), before)
        self.assertFalse(list(self.project.data_dir.glob("tmp*")))

    def test_failed_compact_checkpoint_does_not_mutate_live_session(self):
        before = copy.deepcopy(self.session)
        with (patch("session.save_state", side_effect=OSError("disk failure")),
              self.assertRaises(OSError)):
            prepare_context(self.project, self.session, summary, force=True)
        self.assertEqual(self.session, before)

    def test_missing_handoff_does_not_prevent_reopening(self):
        rollover_session(self.project, self.session, summary)
        self.project.handoff_file.unlink()
        (self.project.data_dir / self.session.last_handoff).unlink()
        restored = load_session(self.project)
        bootstrap_session(self.project, restored)
        self.assertEqual(restored.id, 2)
        self.assertIn("Directory entries", restored.messages[0]["content"])

    def test_tools_read_write_edit_glob_and_boundary(self):
        write_file(self.project, "src/a.py", "first\nsecond\nthird\n")
        self.assertEqual(read_file(self.project, "src/a.py", 1, 1),
                         "2: second\n\n[More lines; continue with offset=2]")
        edit_file(self.project, "src/a.py", "second", "changed")
        self.assertIn("changed", (self.repo / "src/a.py").read_text())
        self.assertEqual(glob(self.project, "**/*.py"), "src/a.py")
        write_file(self.project, "duplicate.txt", "same same")
        with self.assertRaises(ValueError):
            edit_file(self.project, "duplicate.txt", "same", "different")
        other = self.other_project()
        (other.root / "secret.txt").write_text("private")
        (self.repo / "escape").symlink_to(other.root, target_is_directory=True)
        for path in ("../repo-b/secret.txt", "escape/secret.txt", str(other.root / "secret.txt")):
            with self.assertRaises(ValueError):
                read_file(self.project, path)
            with self.assertRaises(ValueError):
                write_file(self.project, path, "changed")
        self.assertNotIn("secret.txt", glob(self.project, "**/*.txt"))
        (self.repo / "recursive").symlink_to(self.repo, target_is_directory=True)
        self.assertEqual(glob(self.project, "**/*.py"), "src/a.py")
        with self.assertRaises(ValueError):
            glob(self.project, "../**")
        with self.assertRaises(ValueError):
            read_file(self.project, "agent://../project-b/state.json")
        self.assertEqual((other.root / "secret.txt").read_text(), "private")

    def test_project_file_writes_are_atomic_and_preserve_mode(self):
        path = self.repo / "script.sh"
        path.write_text("old")
        path.chmod(0o755)
        with (patch("session.os.replace", side_effect=OSError("disk full")),
              self.assertRaises(OSError)):
            write_file(self.project, "script.sh", "new")
        self.assertEqual(path.read_text(), "old")
        self.assertEqual(path.stat().st_mode & 0o777, 0o755)
        edit_file(self.project, "script.sh", "old", "new")
        self.assertEqual(path.read_text(), "new")
        self.assertEqual(path.stat().st_mode & 0o777, 0o755)

    def test_read_and_history_scans_have_limits(self):
        path = self.repo / "large.txt"
        path.write_text("a" * 100_001)
        with self.assertRaisesRegex(ValueError, "size limit"):
            read_file(self.project, "large.txt")
        with self.assertRaises(ValueError):
            read_file(self.project, "large.txt", limit=1001)
        history = self.project.transcripts_dir / "session_999.jsonl"
        history.write_text("needle\n" * 100)
        with patch("session.MAX_HISTORY_SCAN_BYTES", 20):
            result = search_history(self.project, "needle")
        self.assertIn("Search stopped at the history scan limit", result)

    def test_bash_can_be_cancelled(self):
        from tools import RunCancelled

        cancel = Event()
        thread = Thread(target=lambda: (cancel.wait(0.15), cancel.set()))
        thread.start()
        try:
            with self.assertRaises(RunCancelled):
                run_bash(self.project, "sleep 5", cancel_event=cancel)
        finally:
            thread.join()

    def test_agent_document_writes_and_protected_runtime_data(self):
        write_file(self.project, "agent://PROJECT.md", "Keep changes concise")
        edit_file(self.project, "agent://PROJECT.md", "concise", "focused")
        self.assertIn("Keep changes focused", read_file(self.project, "agent://PROJECT.md"))
        self.assertEqual(self.project.project_file.read_text(), "Keep changes focused")
        self.assertFalse((self.repo / "agent:").exists())
        state = self.project.state_file.read_text()
        for path in ("agent://state.json", "agent://HANDOFF.md", "agent://transcripts/new.jsonl",
                     "agent://handoffs/new.md", "agent://../outside.md"):
            with self.subTest(path=path):
                with self.assertRaises(ValueError):
                    write_file(self.project, path, "changed")
                with self.assertRaises(ValueError):
                    edit_file(self.project, path, "old", "changed")
        self.project.project_file.unlink()
        self.project.project_file.symlink_to(self.project.state_file)
        with self.assertRaises(ValueError):
            write_file(self.project, "agent://PROJECT.md", "changed")
        self.assertEqual(self.project.state_file.read_text(), state)

    def test_structured_tool_events_include_target_status_and_bounded_output(self):
        events = []
        agent = Agent(on_event=events.append)
        for name, args, status in (
            ("bash", {"command": "printf '%02000d' 0"}, "completed"),
            ("read_file", {"path": "missing.txt"}, "failed"),
        ):
            agent.run_tool(self.project, self.session, {"id": name, "name": name, "input": args})
            start, end = events[-2:]
            self.assertEqual(start["status"], "running")
            self.assertEqual(end["status"], status)
            self.assertEqual(start["target"], next(iter(args.values())))
            self.assertEqual(start["tool_call_id"], end["tool_call_id"])
            self.assertGreaterEqual(end["duration_ms"], 0)
            self.assertLessEqual(len(end["output"]), 1000)

    def test_bash_uses_current_project_and_reports_errors(self):
        self.assertEqual(run_bash(self.project, "pwd").strip(), str(self.repo))
        with self.assertRaisesRegex(RuntimeError, "status 7"):
            run_bash(self.project, "echo failure >&2; exit 7")
        with self.assertRaisesRegex(RuntimeError, "timed out"):
            run_bash(self.project, "sleep 5", timeout=1)

    def test_bash_stops_oversized_output(self):
        with (patch("tools.MAX_BASH_OUTPUT_BYTES", 1024),
              self.assertRaisesRegex(RuntimeError, "output exceeded 1024 bytes")):
            run_bash(self.project, "printf '%02000d' 0")

    def test_large_output_is_saved_in_full_and_readable(self):
        block = {"id": "big", "name": "bash", "input": {"command": "printf '%012000d' 0"}}
        result = execute_tool(self.project, self.session, block, output_limit=1000)
        self.assertLessEqual(len(result["content"]), 1000)
        saved = list((self.project.data_dir / "tool-results").glob("*.txt"))
        self.assertEqual(len(saved), 1)
        self.assertEqual(len(saved[0].read_text()), 12000)
        self.assertIn("agent://tool-results/", result["content"])
        self.assertIn("00000", read_file(self.project, "agent://tool-results/" + saved[0].name))

    def test_invalid_tool_is_an_error_result(self):
        result = execute_tool(self.project, self.session,
                              {"id": "bad", "name": "unknown", "input": {}})
        self.assertTrue(result["is_error"])
        self.assertEqual(result["tool_use_id"], "bad")
        result = execute_tool(self.project, self.session,
                              {"id": "bad", "name": "bash", "input": {"command": "true", "extra": True}})
        self.assertTrue(result["is_error"])

    def test_tool_set_is_only_v1(self):
        self.assertEqual({t["name"] for t in TOOLS},
                         {"bash", "read_file", "write_file", "edit_file", "glob", "todo_write", "search_history"})

    def test_agent_tool_roundtrip_and_completion(self):
        client = FakeClient([
            response(calls=[("write_file", {"path": "made.txt", "content": "done"}),
                            ("read_file", {"path": "made.txt"})]), response(),
        ])
        agent = Agent(client, "fake-model", emit=lambda _: None)
        agent.run(self.project, self.session, "Create a file")
        self.assertEqual((self.repo / "made.txt").read_text(), "done")
        self.assertEqual(self.session.active_request, "")
        self.assertEqual(read_json(self.project.state_file)["active_request"], "")
        results = client.calls[1]["messages"][-1]["content"]
        self.assertEqual([r["tool_use_id"] for r in results], ["call_0", "call_1"])
        self.assertIn("1: done", results[1]["content"])

    def test_agent_reactive_compact_then_rollover_and_resume(self):
        client = FakeClient([
            RuntimeError("context_length_exceeded"), response("Compact reference"),
            RuntimeError("prompt is too long"), response(summary("handoff", "")), response(),
        ])
        agent = Agent(client, "fake-model", emit=lambda _: None)
        agent.run(self.project, self.session, "Continue until finished")
        self.assertEqual((self.session.id, self.session.compact_count), (2, 0))
        self.assertTrue(self.project.handoff_file.exists())
        self.assertIn("Continue until finished", client.calls[-1]["messages"][0]["content"])
        self.assertEqual(len(client.calls[-1]["messages"]), 1)

    def test_repeated_context_errors_are_bounded(self):
        client = FakeClient([
            RuntimeError("context_length_exceeded"), response("summary"),
            RuntimeError("context_length_exceeded"), response(summary("handoff", "")),
            RuntimeError("context_length_exceeded"),
        ])
        with self.assertRaisesRegex(RuntimeError, "context_length_exceeded"):
            Agent(client, "fake", emit=lambda _: None).run(self.project, self.session, "Do work")
        self.assertEqual(self.session.id, 2)
        self.assertEqual(self.session.active_request, "Do work")

    def test_summary_context_rejection_retries_with_smaller_input(self):
        client = FakeClient([RuntimeError("prompt is too long"), response("summary")])
        result = Agent(client, "fake").summarize("Summarize facts", "history " * 1000)
        self.assertEqual(result, "summary")
        self.assertLess(len(client.calls[1]["messages"][0]["content"]),
                        len(client.calls[0]["messages"][0]["content"]))

    def test_truncated_summary_retries_with_independent_budget(self):
        client = FakeClient([response("partial", stop="max_tokens"), response("Complete summary")])
        events = []
        agent = Agent(client, "fake", max_tokens=100, summary_max_tokens=3000, on_event=events.append)
        self.assertEqual(agent.summarize("Summarize facts", "history"), "Complete summary")
        self.assertEqual([call["max_tokens"] for call in client.calls], [2000, 3000])
        self.assertEqual(events[0]["status"], "retrying")
        self.assertEqual(events[0]["max_tokens"], 3000)

    def test_summary_retry_exhaustion_preserves_compact_and_rollover_state(self):
        for compact_count in (0, 1):
            with self.subTest(compact_count=compact_count):
                self.session.compact_count = compact_count
                save_state(self.project, self.session)
                before = copy.deepcopy(self.session)
                checkpoint = self.project.state_file.read_text()
                client = FakeClient([response("partial", stop="max_tokens")] * 3)
                events = []
                agent = Agent(client, "fake", summary_max_tokens=16000, on_event=events.append)
                with self.assertRaisesRegex(RuntimeError, "SUMMARY_MAX_TOKENS=16000 after 3 attempts"):
                    agent.prepare(self.project, self.session, force=True)
                self.assertEqual([call["max_tokens"] for call in client.calls], [2000, 4000, 16000])
                self.assertEqual(self.session, before)
                self.assertEqual(self.project.state_file.read_text(), checkpoint)
                self.assertFalse(self.project.handoff_file.exists())
                self.assertEqual(events[0]["status"], "running")
                self.assertEqual(events[-1]["status"], "failed")

    def test_oversized_bootstrap_stops_rollover_churn(self):
        self.session.compact_count = 1
        self.session.active_request = "Long request " * 1000
        with self.assertRaisesRegex(ValueError, "Fresh project context"):
            Agent(FakeClient([response(summary("handoff", ""))]), "fake", context_limit=2000,
                  emit=lambda _: None).prepare(self.project, self.session, force=True)
        self.assertEqual(self.session.id, 2)

    def test_other_api_errors_do_not_compact(self):
        client = FakeClient([RuntimeError("authentication failed")])
        with self.assertRaisesRegex(RuntimeError, "authentication"):
            Agent(client, "fake").run(self.project, self.session, "Keep this request")
        self.assertEqual(self.session.compact_count, 0)
        self.assertEqual(load_session(self.project).active_request, "Keep this request")

    def test_truncated_responses_continue_without_clearing_request(self):
        client = FakeClient([response("Partial", stop="max_tokens"), response("Complete")])
        Agent(client, "fake", emit=lambda _: None).run(self.project, self.session, "Do work")
        self.assertEqual(len(client.calls), 2)
        self.assertIn("Continue the unfinished", client.calls[1]["messages"][-1]["content"])
        self.assertEqual(self.session.active_request, "")

    def test_truncated_tool_is_never_executed(self):
        client = FakeClient([
            response("Discard this text too", calls=[
                ("write_file", {"path": "bad.txt", "content": "bad"}),
                ("write_file", {"path": "also-bad.txt", "content": "bad"}),
            ], stop="max_tokens"),
            response(calls=[("write_file", {"path": "good.txt", "content": "small section"})]),
            response(),
        ])
        emitted, events = [], []
        Agent(client, "fake", emit=emitted.append, on_event=events.append).run(
            self.project, self.session, "Do work")
        self.assertFalse((self.repo / "bad.txt").exists())
        self.assertFalse((self.repo / "also-bad.txt").exists())
        self.assertEqual((self.repo / "good.txt").read_text(), "small section")
        self.assertNotIn("Discard this text too", emitted)
        retry_context = client.calls[1]["messages"]
        self.assertEqual(retry_context[-1]["role"], "user")
        self.assertIn("NONE of its tool calls were executed", retry_context[-1]["content"])
        self.assertIn("ONE small tool call", retry_context[-1]["content"])
        self.assertNotIn("bad.txt", json.dumps(retry_context))
        self.assertEqual(client.calls[0]["max_tokens"], client.calls[1]["max_tokens"])
        self.assertEqual(events[0]["activity"], "tool_input")
        self.assertEqual(events[0]["attempt"], 2)
        self.assertEqual((self.session.id, self.session.compact_count), (1, 0))
        self.assertEqual(load_session(self.project).active_request, "")

    def test_truncated_tool_retries_are_bounded_and_request_can_resume(self):
        # Output exhaustion must not force the next context transition.
        self.session.compact_count = 1
        failed = response(calls=[("write_file", {"path": "bad.txt", "content": "bad"})],
                          stop="max_tokens")
        client = FakeClient([failed, failed, failed, response()])
        events = []
        agent = Agent(client, "fake", emit=lambda _: None, on_event=events.append)
        with self.assertRaisesRegex(RuntimeError, "MAX_TOKENS after 3 attempts; request retained"):
            agent.run(self.project, self.session, "Write the paper")
        self.assertEqual(len(client.calls), 3)
        self.assertEqual([event["attempt"] for event in events], [2, 3])
        self.assertFalse((self.repo / "bad.txt").exists())
        self.assertEqual((self.session.id, self.session.compact_count), (1, 1))
        self.assertFalse(self.project.handoff_file.exists())
        self.assertEqual(load_session(self.project).active_request, "Write the paper")
        self.assertEqual(self.session.messages[-1]["role"], "user")
        agent.run(self.project, self.session)
        self.assertEqual(len(client.calls), 4)
        self.assertIn("ONE small tool call", client.calls[-1]["messages"][-1]["content"])
        self.assertEqual(load_session(self.project).active_request, "")

    def test_completed_tool_call_resets_truncated_tool_retry_count(self):
        failed = response(calls=[("write_file", {"path": "bad.txt", "content": "bad"})],
                          stop="max_tokens")
        client = FakeClient([
            failed, failed,
            response(calls=[("write_file", {"path": "draft.md", "content": "Section one"})]),
            failed, failed,
            response(calls=[("edit_file", {"path": "draft.md", "old_text": "Section one",
                                          "new_text": "Section one\n\nSection two"})]),
            response(),
        ])
        events = []
        Agent(client, "fake", emit=lambda _: None, on_event=events.append).run(
            self.project, self.session, "Write two sections")
        self.assertEqual((self.repo / "draft.md").read_text(), "Section one\n\nSection two")
        self.assertFalse((self.repo / "bad.txt").exists())
        self.assertEqual([e["attempt"] for e in events if e["activity"] == "tool_input"],
                         [2, 3, 2, 3])
        self.assertEqual(self.session.active_request, "")

    def test_interrupt_closes_all_tool_calls(self):
        client = FakeClient([response(calls=[("bash", {"command": "true"}), ("glob", {"pattern": "*"})])])
        with (patch("agent.execute_tool", side_effect=KeyboardInterrupt),
              self.assertRaises(KeyboardInterrupt)):
            Agent(client, "fake", emit=lambda _: None).run(self.project, self.session, "Do work")
        results = self.session.messages[-1]["content"]
        self.assertEqual(len(results), 2)
        self.assertTrue(all(r["is_error"] for r in results))
        self.assertEqual(self.session.active_request, "Do work")

    def test_cancelled_bash_keeps_valid_tool_history_and_request(self):
        from tools import RunCancelled

        client = FakeClient([response(calls=[("bash", {"command": "sleep 5"}),
                                              ("glob", {"pattern": "*"})])])
        cancel = Event()
        agent = Agent(client, "fake", emit=lambda _: None)
        agent.cancel_event = cancel
        thread = Thread(target=lambda: (cancel.wait(0.15), cancel.set()))
        thread.start()
        try:
            with self.assertRaises(RunCancelled):
                agent.run(self.project, self.session, "Do work")
        finally:
            thread.join()
        results = self.session.messages[-1]["content"]
        self.assertEqual(len(results), 2)
        self.assertTrue(all(result["is_error"] for result in results))
        self.assertEqual(load_session(self.project).active_request, "Do work")

    def test_cli_without_credentials_and_quoted_paths(self):
        spaced = self.base / "repo with spaces"
        spaced.mkdir()
        app = Application(self.registry.home)
        with io.StringIO() as output, patch("sys.stdout", output):
            self.assertTrue(app.command(f'new "Quoted Project" "{spaced}"'))
            app.command("/project current")
            self.assertIn("Quoted Project", output.getvalue())
            app.command("/back")
            self.assertIsNone(app.project)
            self.assertFalse(app.command("exit"))
        app.close()
        self.registry.close()
        result = subprocess.run(
            [os.sys.executable, str(Path(__file__).resolve().parents[1] / "main.py"),
             "--data-home", str(self.registry.home)],
            input="list\nexit\n", capture_output=True, text=True, check=False,
            env={**os.environ, "MODEL_ID": "", "ANTHROPIC_API_KEY": ""},
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("project-a", result.stdout)


if __name__ == "__main__":
    unittest.main()
