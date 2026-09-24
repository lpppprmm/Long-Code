"""Durable task notes and runtime evidence, independent of model context windows."""

import hashlib
import json
import re
from uuid import uuid4

from session import preview, read_json, safe_path, timestamp, write_json

NOTE_KINDS = ("constraint", "decision", "finding", "failed_attempt")
NOTE_STATUSES = ("active", "unverified", "superseded")
TASK_MARKER = "\n\n<!-- runtime-task-state -->\n"
MAX_ACTIVE_NOTES = 24
MAX_HASH_BYTES = 4 * 1024 * 1024


def task_path(project, task_id, relative="task.json"):
    if not re.fullmatch(r"[a-f0-9]{32}", task_id):
        raise ValueError("Invalid task ID")
    return safe_path(project.data_dir, f"tasks/{task_id}/{relative}")


def task_uri(task_id, relative="task.json"):
    return f"agent://tasks/{task_id}/{relative}"


def load_task(project, session):
    if not session.task_id:
        return None
    task = read_json(task_path(project, session.task_id))
    if not isinstance(task, dict) or task.get("version") != 1 or task.get("id") != session.task_id:
        raise ValueError("Missing or invalid durable task record")
    return task


def record_request(project, session, request, *, new_task=False):
    """Write the original request before publishing its reference in task/session state."""
    task = None if new_task else load_task(project, session)
    if task is None:
        task = {"version": 1, "id": uuid4().hex, "requests": [], "notes": {},
                "next_action": None, "recent_evidence": [], "revision": 0}
    source = f"request-{len(task['requests']) + 1:04d}"
    write_json(task_path(project, task["id"], f"{source}.json"), {
        "id": source, "kind": "request", "text": request,
        "session": session.id, "time": timestamp(),
    })
    task["requests"].append({"id": source, "preview": preview(request, 500)})
    task["revision"] += 1
    write_json(task_path(project, task["id"]), task)
    session.task_id = task["id"]
    return source


def fingerprints(project, paths):
    """Bounded file observations also work in projects without Git."""
    result = {}
    for name in sorted(set(paths))[:100]:
        try:
            path = safe_path(project.root, name)
            if not path.is_file():
                result[name] = "missing"
            elif path.stat().st_size > MAX_HASH_BYTES:
                result[name] = "too-large-to-hash"
            else:
                digest = hashlib.sha256()
                with path.open("rb") as handle:
                    for chunk in iter(lambda: handle.read(65536), b""):
                        digest.update(chunk)
                result[name] = digest.hexdigest()
        except (OSError, ValueError):
            result[name] = "unavailable"
    return result


def record_evidence(project, session, block, output, status, metadata=None):
    task = load_task(project, session)
    if task is None:
        return None
    source = "evidence-" + uuid4().hex
    args = block["input"] if isinstance(block["input"], dict) else {}
    paths = list(session.changed_files)
    paths.extend((task["next_action"] or {}).get("files", []))
    path = args.get("path")
    if isinstance(path, str) and not path.startswith("agent://"):
        paths.append(path)
    record = {"id": source, "kind": "tool", "session": session.id, "time": timestamp(),
              "tool": block["name"], "tool_call_id": block["id"], "input": args,
              "status": status, "output": output, "execution": metadata or {},
              "files": fingerprints(project, paths),
              "file_scope": "Recorded paths after execution; other files and environment are not verified."}
    write_json(task_path(project, task["id"], f"{source}.json"), record)
    target = next((str(args[key]) for key in ("command", "path", "pattern", "query") if key in args), "")
    task["recent_evidence"] = [*task["recent_evidence"][-5:], {
        "id": source, "tool": block["name"], "status": status, "target": preview(target, 180),
    }]
    task["revision"] += 1
    write_json(task_path(project, task["id"]), task)
    return source


def source_record(project, task, source):
    if not isinstance(source, str) or not re.fullmatch(r"request-\d{4,}|evidence-[a-f0-9]{32}", source):
        raise ValueError("Evidence must reference a request or tool evidence ID from this task")
    record = read_json(task_path(project, task["id"], f"{source}.json"))
    if not isinstance(record, dict) or record.get("id") != source:
        raise ValueError(f"Unknown task evidence: {source}")
    return record


def _text(value, name, limit):
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise ValueError(f"{name} must be nonempty text of at most {limit} characters")


def _sources(project, task, sources):
    if not isinstance(sources, list) or len(sources) > 5:
        raise ValueError("Use at most five evidence IDs per record")
    return [source_record(project, task, source) for source in sources]


def update_task(project, session, notes=None, next_action=None):
    """Upsert individual notes; preserve all omitted notes and the last next action."""
    task = load_task(project, session)
    if task is None or not session.active_request:
        raise ValueError("Start a request before updating its task record")
    if notes is None and next_action is None:
        raise ValueError("Provide notes or a next_action")
    if notes is not None:
        if not isinstance(notes, list) or not 1 <= len(notes) <= 10:
            raise ValueError("Update one to ten notes at a time")
        seen = set()
        for note in notes:
            if not isinstance(note, dict) or set(note) != {"id", "kind", "text", "status", "evidence"}:
                raise ValueError("Notes require id, kind, text, status, and evidence")
            key = note["id"]
            if not isinstance(key, str) or not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}", key) or key in seen:
                raise ValueError("Use unique stable note IDs of at most 64 letters, digits, dashes or underscores")
            seen.add(key)
            if note["kind"] not in NOTE_KINDS or note["status"] not in NOTE_STATUSES:
                raise ValueError("Invalid note kind or status")
            _text(note["text"], "Note text", 600)
            sources = _sources(project, task, note["evidence"])
            if note["status"] != "unverified" and not sources:
                raise ValueError("Active or superseded notes require source evidence")
            if note["kind"] == "constraint" and not any(s["kind"] == "request" for s in sources):
                raise ValueError("Constraints require an original user request as evidence")
            if note["kind"] in ("finding", "failed_attempt") and note["status"] == "active" and not any(s["kind"] == "tool" for s in sources):
                raise ValueError("Active findings and failed attempts require observed tool evidence")
            previous = task["notes"].get(key)
            if previous and previous["kind"] != note["kind"]:
                raise ValueError("A note's kind cannot change; create a new note ID")
            task["notes"][key] = {**note, "session": session.id, "updated_at": timestamp()}
        if sum(n["status"] != "superseded" for n in task["notes"].values()) > MAX_ACTIVE_NOTES:
            raise ValueError(f"Keep at most {MAX_ACTIVE_NOTES} current notes; supersede obsolete notes explicitly")
    if next_action is not None:
        if not isinstance(next_action, dict) or set(next_action) != {"action", "verification", "files", "evidence"}:
            raise ValueError("next_action requires action, verification, files, and evidence")
        _text(next_action["action"], "Next action", 600)
        _text(next_action["verification"], "Verification", 600)
        if not isinstance(next_action["files"], list) or len(next_action["files"]) > 5:
            raise ValueError("Use at most five files for the next action")
        for path in next_action["files"]:
            _text(path, "File path", 500)
            safe_path(project.root, path)
        _sources(project, task, next_action["evidence"])
        task["next_action"] = next_action
    task["revision"] += 1
    write_json(task_path(project, task["id"]), task)
    return f"Updated task revision {task['revision']}: {task_uri(task['id'])}"


def task_context(project, session, limit=6000, *, verify=False):
    task = load_task(project, session)
    if task is None:
        return ""
    parts = [f"Durable task record: {task_uri(task['id'])} (revision {task['revision']}).",
             "Notes are model-authored claims. User requests and observed evidence take precedence. "
             "Read source IDs at agent://tasks/" + task["id"] + "/<source-id>.json. "
             "Before editing, check evidence relevant to the next action. Matching files do not prove correctness."]
    action = task["next_action"]
    parts.append("Next action: " + (json.dumps(action, ensure_ascii=False) if action else
                                  "Not recorded. Inspect the unfinished request and establish a concrete next action."))
    requests = task["requests"]
    selected = requests if len(requests) <= 2 else [requests[0], requests[-1]]
    for request in selected:
        parts.append(f"Original user source {request['id']}: {request['preview']}")
    if len(requests) > len(selected):
        parts.append(f"{len(requests) - len(selected)} other user sources are listed in the task record; consult them for changed requirements.")
    omitted = False
    sources = list((action or {}).get("evidence", []))
    notes = sorted((n for n in task["notes"].values() if n["status"] != "superseded"),
                   key=lambda n: n["kind"] != "constraint")
    for note in notes:
        text = f"[{note['id']}] {note['kind']} / {note['status']}: {note['text']} (sources: {', '.join(note['evidence']) or 'none'})"
        parts.append(text)
        sources.extend(note["evidence"])
    verification = []
    if verify:
        changed = []
        incomplete = False
        evidence_ids = list(dict.fromkeys(s for s in sources if s.startswith("evidence-")))
        for source in evidence_ids[:6]:
            try:
                record = source_record(project, task, source)
                old = record.get("files", {})
                current = fingerprints(project, old)
                differences = [path for path in old if current[path] != old[path]]
                if differences:
                    changed.append(f"{source}: " + ", ".join(differences))
                incomplete |= not old or any(value in ("unavailable", "too-large-to-hash") for value in current.values())
            except (OSError, ValueError):
                changed.append(f"{source}: evidence unavailable")
        if changed:
            verification.append("Evidence needs rechecking (files changed or source unavailable): " + preview("; ".join(changed), 800))
        elif evidence_ids:
            verification.append("Compared recorded file fingerprints; this does not verify tests or unrecorded dependencies.")
        if incomplete or len(evidence_ids) > 6:
            verification.append("Evidence verification is partial; inspect remaining sources as needed.")
    # Show stale-evidence warnings before lower-priority notes and recent commands.
    parts[2:2] = verification
    for item in task["recent_evidence"][-3:]:
        parts.append(f"Recent evidence {item['id']}: {item['tool']} {item['status']} {item['target']}")
    notice = "Required before editing: some task details were omitted for space. Read the linked task record and relevant original sources."
    retained = []
    used = 0
    for part in parts:
        if used + len(part) + 2 <= limit - len(notice) - 2:
            retained.append(part)
            used += len(part) + 2
        else:
            omitted = True
    if omitted:
        retained.append(notice)
    return "\n\n".join(retained)


def task_document(project, session):
    """Human-readable view of the same canonical record, without bootstrap instructions."""
    task = load_task(project, session)
    if task is None:
        return ""
    parts = ["# 任务记录", "状态：" + ("进行中" if session.active_request else "本次请求已结束")]
    action = task["next_action"]
    if action:
        parts.extend(["## 下一步" if session.active_request else "## 最近记录的下一步",
                      action["action"], "验证方式：" + action["verification"]])
        if action["files"]:
            parts.append("相关文件：" + "、".join(action["files"]))
        if action["evidence"]:
            parts.append("证据：" + "、".join(action["evidence"]))
    else:
        parts.extend(["## 下一步", "尚未记录。"])
    labels = {"constraint": "有效约束", "decision": "关键决策", "finding": "发现与判断",
              "failed_attempt": "失败尝试"}
    for kind, label in labels.items():
        notes = [n for n in task["notes"].values() if n["kind"] == kind and n["status"] != "superseded"]
        if notes:
            parts.append("## " + label)
            for note in notes:
                parts.append(("待验证：" if note["status"] == "unverified" else "") + note["text"])
                if note["evidence"]:
                    parts.append("来源：" + "、".join(note["evidence"]))
    if task["recent_evidence"]:
        statuses = {"completed": "已执行", "failed": "失败", "interrupted": "已中断"}
        parts.append("## 最近的执行证据")
        for item in task["recent_evidence"]:
            parts.append(f"{item['tool']}（{statuses[item['status']]}）：{item['target']}\n\n来源：{item['id']}")
    parts.extend(["记录中的判断需要结合原文和实际文件核对；命令成功退出不代表功能已经通过验证。",
                  f"完整任务记录：`{task_uri(task['id'])}`",
                  f"原始请求与证据：`agent://tasks/{task['id']}/<来源编号>.json`"])
    return "\n\n".join(parts)
