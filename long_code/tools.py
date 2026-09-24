"""Synchronous project tools and incremental durable task updates."""

import json
import os
import selectors
import signal
import subprocess
from pathlib import Path
from time import monotonic
from uuid import uuid4

from .config import MAX_TOOL_OUTPUT
from .history import search_history
from .session import save_state
from .storage import atomic_write, safe_path
from .task_state import (
    NOTE_KINDS,
    NOTE_STATUSES,
    record_evidence,
    task_uri,
    update_task,
)

MAX_BASH_OUTPUT_BYTES = 10 * 1024 * 1024
MAX_READ_LINES = 1000
MAX_READ_CHARS = 100_000
MAX_READ_OFFSET = 100_000


class RunCancelled(Exception):
    """Raised when a user stops a running agent request."""


def tool(name, description, properties, required):
    return {"name": name, "description": description, "input_schema": {
        "type": "object", "properties": properties, "required": required,
        "additionalProperties": False,
    }}


STRING = {"type": "string"}
TOOLS = [
    tool("bash", "Run synchronous Bash in the project root. This is not a sandbox.",
         {"command": STRING, "timeout": {"type": "number", "minimum": 1, "maximum": 600}}, ["command"]),
    tool("read_file", "Read a project file with zero-based line offset. For current-project "
         "agent data, use agent://PROJECT.md, agent://HANDOFF.md or agent://tool-results/<file>.",
         {"path": STRING, "offset": {"type": "integer", "minimum": 0, "maximum": MAX_READ_OFFSET},
          "limit": {"type": "integer", "minimum": 1, "maximum": MAX_READ_LINES}}, ["path"]),
    tool("write_file", "Write a UTF-8 project file or agent://PROJECT.md; other agent data is read-only.",
         {"path": STRING, "content": STRING}, ["path", "content"]),
    tool("edit_file", "Replace exactly one old_text in a project file or agent://PROJECT.md; other agent data is read-only.",
         {"path": STRING, "old_text": STRING, "new_text": STRING}, ["path", "old_text", "new_text"]),
    tool("glob", "Find paths inside the project; narrow the pattern if results are truncated.",
         {"pattern": STRING}, ["pattern"]),
    tool("todo_write", "Replace this session's todos. Use at most one in_progress item.",
         {"todos": {"type": "array", "items": {"type": "object", "properties": {
             "content": STRING, "status": {"type": "string", "enum": ["pending", "in_progress", "completed"]}},
             "required": ["content", "status"], "additionalProperties": False}}}, ["todos"]),
    tool("search_history", "Search only this project's historical handoffs and transcripts.",
         {"query": STRING, "limit": {"type": "integer", "minimum": 1, "maximum": 20}}, ["query"]),
    tool("task_update", "Incrementally preserve task notes across compaction and rollover. "
         "Upsert notes by stable ID; omitted notes and next_action remain unchanged. "
         "Cite request/evidence IDs from this task. Constraints need a user request source. "
         "Active means still relevant, not proven; use unverified for hypotheses, superseded for obsolete notes. "
         "Record a specific next action and its verification condition after meaningful progress.", {
             "notes": {"type": "array", "minItems": 1, "maxItems": 10, "items": {
                 "type": "object", "additionalProperties": False, "properties": {
                     "id": {"type": "string", "maxLength": 64},
                     "kind": {"type": "string", "enum": list(NOTE_KINDS)},
                     "text": {"type": "string", "maxLength": 600},
                     "status": {"type": "string", "enum": list(NOTE_STATUSES)},
                     "evidence": {"type": "array", "items": STRING, "maxItems": 5},
                 }, "required": ["id", "kind", "text", "status", "evidence"]}},
             "next_action": {"type": "object", "additionalProperties": False, "properties": {
                 "action": {"type": "string", "maxLength": 600},
                 "verification": {"type": "string", "maxLength": 600},
                 "files": {"type": "array", "items": STRING, "maxItems": 5},
                 "evidence": {"type": "array", "items": STRING, "maxItems": 5},
             }, "required": ["action", "verification", "files", "evidence"]},
         }, []),
]


def run_bash(project, command: str, timeout: float = 120, cancel_event=None, *, metadata=None):
    if not isinstance(command, str) or not command.strip() or not 1 <= timeout <= 600:
        raise ValueError("Use a nonempty command and a timeout from 1 to 600 seconds")
    process = subprocess.Popen(
        ["bash", "-c", command], cwd=project.root, stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT, start_new_session=True,
    )
    output = bytearray()
    timed_out = too_large = cancelled = False
    deadline = monotonic() + timeout
    with selectors.DefaultSelector() as selector:
        selector.register(process.stdout, selectors.EVENT_READ)
        try:
            while True:
                if cancel_event is not None and cancel_event.is_set():
                    cancelled = True
                    break
                remaining = deadline - monotonic()
                if remaining <= 0:
                    timed_out = True
                    break
                events = selector.select(timeout=min(remaining, 0.1))
                if not events:
                    if process.poll() is not None:
                        break
                    continue
                chunk = os.read(process.stdout.fileno(), 65536)
                if not chunk:
                    while process.poll() is None:
                        if cancel_event is not None and cancel_event.is_set():
                            cancelled = True
                            break
                        remaining = deadline - monotonic()
                        if remaining <= 0:
                            timed_out = True
                            break
                        try:
                            process.wait(timeout=min(remaining, 0.1))
                        except subprocess.TimeoutExpired:
                            pass
                    break
                available = MAX_BASH_OUTPUT_BYTES - len(output)
                output.extend(chunk[:available])
                if len(chunk) > available:
                    too_large = True
                    break
        finally:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait()
            process.stdout.close()
            if metadata is not None:
                metadata.update(exit_code=process.returncode, timed_out=timed_out,
                                output_truncated=too_large, cancelled=cancelled)
    text = output.decode("utf-8", errors="replace") or "(no output)"
    if cancelled:
        raise RunCancelled(f"Run cancelled; unfinished request retained\n{text}")
    if timed_out:
        raise RuntimeError(f"Command timed out after {timeout}s\n{text}")
    if too_large:
        raise RuntimeError(f"Command output exceeded {MAX_BASH_OUTPUT_BYTES} bytes and was stopped\n{text}")
    if process.returncode:
        raise RuntimeError(f"Command exited with status {process.returncode}\n{text}")
    return text


def file_path(project, path: str, *, write=False):
    if path.startswith("agent://"):
        fp = safe_path(project.data_dir, path[len("agent://"):])
    else:
        fp = safe_path(project.root, path)
    data_dir = project.data_dir.resolve()
    if write and fp.is_relative_to(data_dir) and fp != data_dir / "PROJECT.md":
        raise ValueError("Only agent://PROJECT.md is writable; other agent data is managed by the runtime")
    return fp


def read_file(project, path: str, offset: int = 0, limit: int = 200):
    if (type(offset) is not int or type(limit) is not int
            or not 0 <= offset <= MAX_READ_OFFSET or not 1 <= limit <= MAX_READ_LINES):
        raise ValueError(f"offset must be 0–{MAX_READ_OFFSET} and limit must be 1–{MAX_READ_LINES}")
    fp = file_path(project, path)
    with fp.open(encoding="utf-8") as handle:
        for _ in range(offset):
            line = handle.readline(MAX_READ_CHARS + 1)
            if not line:
                return "(no lines)"
            if len(line) > MAX_READ_CHARS and not line.endswith("\n"):
                raise ValueError("A line exceeds the read_file size limit")
        lines = []
        used = 0
        for _ in range(limit):
            line = handle.readline(MAX_READ_CHARS - used + 1)
            if not line:
                break
            if len(line) > MAX_READ_CHARS - used:
                if not lines:
                    raise ValueError("A line exceeds the read_file size limit; use bash for focused byte ranges")
                break
            lines.append(line)
            used += len(line)
            if used == MAX_READ_CHARS:
                break
        more = bool(handle.read(1)) if len(lines) == limit or used == MAX_READ_CHARS else bool(line)
    result = "".join(f"{offset + i + 1}: {line}" for i, line in enumerate(lines))
    if more:
        result += f"\n[More lines; continue with offset={offset + len(lines)}]"
    return result or "(no lines)"


def write_file(project, path: str, content: str):
    fp = file_path(project, path, write=True)
    fp.parent.mkdir(parents=True, exist_ok=True)
    atomic_write(fp, content, preserve_mode=True)
    return f"Wrote {len(content)} characters to {path}"


def edit_file(project, path: str, old_text: str, new_text: str):
    fp = file_path(project, path, write=True)
    content = fp.read_text(encoding="utf-8")
    if not old_text or content.count(old_text) != 1:
        raise ValueError("old_text must match exactly once; include more surrounding context")
    atomic_write(fp, content.replace(old_text, new_text, 1), preserve_mode=True)
    return f"Edited {path}"


def glob(project, pattern: str):
    if Path(pattern).is_absolute() or ".." in Path(pattern).parts:
        raise ValueError("Glob patterns must stay inside the project")
    matches = []
    # pathlib's recursive glob does not recurse through directory symlinks.
    for match in project.root.glob(pattern):
        if match.resolve().is_relative_to(project.root):
            matches.append(str(match.relative_to(project.root)))
            if len(matches) == 201:
                return "\n".join(sorted(matches[:200])) + "\n[More matches; narrow the pattern]"
    return "\n".join(sorted(matches)) or "(no matches)"


def todo_write(project, session, todos: list):
    if not isinstance(todos, list) or any(
        not isinstance(t, dict) or not isinstance(t.get("content"), str)
        or not t["content"].strip() or t.get("status") not in ("pending", "in_progress", "completed")
        for t in todos
    ):
        raise ValueError("Todos need nonempty content and a valid status")
    if sum(t["status"] == "in_progress" for t in todos) > 1:
        raise ValueError("Only one todo may be in progress")
    session.todos = [{"content": t["content"], "status": t["status"]} for t in todos]
    save_state(project, session)
    return json.dumps(session.todos, ensure_ascii=False)


def persist_output(project, output: str, limit=MAX_TOOL_OUTPUT):
    if len(output) <= limit:
        return output
    relative = f"tool-results/{uuid4().hex}.txt"
    atomic_write(safe_path(project.data_dir, relative), output)
    marker = f"\n[Full output saved at agent://{relative}; use read_file to inspect it]\n"
    return output[:max(0, limit - len(marker))] + marker


def execute_tool(project, session, block, output_limit=MAX_TOOL_OUTPUT, cancel_event=None):
    metadata = {}
    handlers = {
        "bash": lambda **args: run_bash(project, cancel_event=cancel_event, metadata=metadata, **args),
        "read_file": lambda **args: read_file(project, **args),
        "write_file": lambda **args: write_file(project, **args),
        "edit_file": lambda **args: edit_file(project, **args),
        "glob": lambda **args: glob(project, **args),
        "todo_write": lambda **args: todo_write(project, session, **args),
        "search_history": lambda **args: search_history(project, **args),
        "task_update": lambda **args: update_task(project, session, **args),
    }
    result = {"type": "tool_result", "tool_use_id": block["id"]}
    try:
        if block["name"] not in handlers:
            raise ValueError(f"Unknown tool: {block['name']}")
        output = handlers[block["name"]](**block["input"])
    except RunCancelled as exc:
        record_evidence(project, session, block, persist_output(project, str(exc), output_limit),
                        "interrupted", metadata)
        raise
    except (OSError, ValueError, TypeError, RuntimeError) as exc:
        output = f"Error: {exc}"
        result["is_error"] = True
    result["content"] = persist_output(project, output, output_limit)
    if session.task_id and block["name"] in ("bash", "read_file", "write_file", "edit_file", "glob", "search_history"):
        source = record_evidence(project, session, block, result["content"],
                                 "failed" if result.get("is_error") else "completed", metadata)
        marker = f"\nEvidence: {source} ({task_uri(session.task_id, source + '.json')})"
        # The evidence file retains this preview and any link to the complete output.
        room = max(0, output_limit - len(marker))
        result["content"] = result["content"][:room] + marker
    return result
