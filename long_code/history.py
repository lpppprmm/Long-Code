"""Append-only transcripts, bounded history search, and session timelines."""

import heapq
import json
import os
import re

from .models import Project, Session
from .storage import preview, read_json, read_preview, safe_path

MAX_HISTORY_SCAN_BYTES = 32 * 1024 * 1024


MAX_HISTORY_LINE_CHARS = 1024 * 1024


def append_transcript(project: Project, session: Session, record: dict):
    project.transcripts_dir.mkdir(parents=True, exist_ok=True)
    path = project.transcript_file(session.id)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def search_history(project: Project, query: str, limit: int = 5):
    if len(query) > 1000:
        raise ValueError("History query must be at most 1000 characters")
    words = set(re.findall(r"\w+", query.lower()))
    if not words or not 1 <= limit <= 20:
        raise ValueError("Use a nonempty query and a limit from 1 to 20")

    remaining = MAX_HISTORY_SCAN_BYTES
    truncated = False

    def fragments():
        nonlocal remaining, truncated
        for directory, pattern in ((project.handoffs_dir, "*.md"),
                                   (project.transcripts_dir, "*.jsonl")):
            for path in sorted(directory.glob(pattern), reverse=True):
                path = safe_path(project.data_dir, str(path))
                with path.open(encoding="utf-8", errors="replace") as handle:
                    number = 0
                    while remaining > 0:
                        line = handle.readline(min(remaining, MAX_HISTORY_LINE_CHARS) + 1)
                        if not line:
                            break
                        number += 1
                        line_bytes = len(line.encode("utf-8"))
                        if (line_bytes > remaining
                                or (len(line) > MAX_HISTORY_LINE_CHARS and not line.endswith("\n"))):
                            truncated = True
                            return
                        remaining -= line_bytes
                        lower = line.lower()
                        score = sum(word in lower for word in words)
                        if score:
                            start = max(0, min(lower.find(w) for w in words if w in lower) - 150)
                            yield (score, str(path.relative_to(project.data_dir)), number,
                                   line[start:start + 1000].strip())
                    if remaining == 0:
                        truncated = True
                        return

    matches = heapq.nlargest(limit, fragments())
    result = "\n\n".join(f"{path}:{number}\n{text}" for _, path, number, text in matches) or "No matching history."
    if truncated:
        result += "\n\n[Search stopped at the history scan limit; narrow the query or inspect files directly.]"
    return result


def list_sessions(project: Project):
    state = read_json(project.state_file)
    if state is None:
        raise ValueError("Project state is missing")
    current = state["current_session"]
    return [{"id": number, "current": number == current,
             "has_handoff": (project.handoffs_dir / f"session_{number:03d}.md").exists(),
             "has_transcript": project.transcript_file(number).exists()}
            for number in range(current, 0, -1)]


def session_history(project: Project, session_id: int, offset=0, limit=50):
    sessions = list_sessions(project)
    if session_id < 1 or session_id > sessions[0]["id"]:
        raise ValueError("Unknown session")
    handoff = project.handoffs_dir / f"session_{session_id:03d}.md"
    transcript = project.transcript_file(session_id)
    records = []
    next_offset = None
    if transcript.exists():
        with transcript.open(encoding="utf-8", errors="replace") as handle:
            for number, line in enumerate(handle):
                if number < offset:
                    continue
                if number >= offset + limit:
                    next_offset = number
                    break
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if record.get("event") == "model_usage":
                    continue
                content = record.get("content", record.get("details", ""))
                if isinstance(content, list):
                    content = "\n".join(
                        block.get("text", "") if block.get("type") == "text"
                        else f"[{block.get('name', block.get('type', 'tool'))}] "
                             + str(block.get("content", ""))
                        for block in content
                    )
                records.append({"line": number + 1, "role": record.get("role", record.get("event", "event")),
                                "text": preview(str(content), 2000)})
    return {"id": session_id, "current": sessions[0]["id"] == session_id,
            "handoff": read_preview(handoff, 20_000) if handoff.exists() else "",
            "checkpoint": read_json(project.checkpoint_file(session_id), {}),
            "records": records, "next_offset": next_offset}
