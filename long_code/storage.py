"""Atomic file operations and bounded reads shared by persistence modules."""

import json
import os
import stat
import tempfile
from datetime import datetime, timezone
from pathlib import Path


def timestamp():
    return datetime.now(timezone.utc).isoformat()


def atomic_write(path: Path, text: str, *, preserve_mode=False):
    path.parent.mkdir(parents=True, exist_ok=True)
    mode = stat.S_IMODE(path.stat().st_mode) if preserve_mode and path.exists() else 0o644
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                     delete=False) as handle:
        temporary = Path(handle.name)
        try:
            if preserve_mode:
                os.fchmod(handle.fileno(), mode)
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise
    try:
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def read_json(path: Path, default=None):
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value):
    atomic_write(path, json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def safe_path(root: Path, path: str) -> Path:
    base = root.resolve()
    resolved = (base / path).resolve()
    if not resolved.is_relative_to(base):
        raise ValueError(f"Path escapes project: {path}")
    return resolved


def preview(text: str, limit: int):
    if len(text) <= limit:
        return text
    marker = "\n[truncated; read the source for more]\n"
    room = max(0, limit - len(marker))
    return text[:room // 2] + marker + text[-(room - room // 2):] if room else marker


def read_preview(path: Path, limit: int):
    with path.open(encoding="utf-8", errors="replace") as handle:
        return preview(handle.read(limit + 1), limit)
