"""Project registration and exclusive leases on the local data home."""

import fcntl
import os
import re
import shutil
import threading
import weakref
from pathlib import Path

from .models import Project, Session
from .session import save_state
from .storage import atomic_write, read_json, safe_path, write_json

_home_leases = {}


_home_leases_lock = threading.Lock()


_home_leases_pid = os.getpid()


def _release_home_lease(home, pid):
    if os.getpid() != pid:
        return
    with _home_leases_lock:
        handle, count = _home_leases[home]
        if count == 1:
            del _home_leases[home]
            handle.close()
        else:
            _home_leases[home] = (handle, count - 1)


def _acquire_home_lease(home):
    global _home_leases_pid
    with _home_leases_lock:
        if _home_leases_pid != os.getpid():
            for handle, _ in _home_leases.values():
                handle.close()
            _home_leases.clear()
            _home_leases_pid = os.getpid()
        if home in _home_leases:
            handle, count = _home_leases[home]
            _home_leases[home] = (handle, count + 1)
        else:
            home.mkdir(parents=True, exist_ok=True)
            handle = (home / ".lock").open("a+")
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                handle.close()
                raise RuntimeError(f"Data home is already in use by another process: {home}") from exc
            _home_leases[home] = (handle, 1)
    return os.getpid()


class Registry:
    def __init__(self, data_home: Path):
        self.home = data_home.expanduser().resolve()
        self.path = self.home / "projects.json"
        pid = _acquire_home_lease(self.home)
        self._lease = weakref.finalize(self, _release_home_lease, self.home, pid)

    def close(self):
        self._lease()

    def entries(self):
        return read_json(self.path, {})

    def open(self, project_id: str):
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]*", project_id):
            raise ValueError("Invalid project ID")
        entry = self.entries().get(project_id)
        if entry is None:
            raise ValueError(f"Unknown project: {project_id}")
        root = Path(entry["root"]).resolve()
        if not root.is_dir():
            raise ValueError(f"Project directory does not exist: {root}")
        return Project(project_id, entry["name"], root,
                       safe_path(self.home / "projects", project_id))

    def create(self, name: str, root: str):
        root = Path(root).expanduser().resolve()
        if not root.is_dir():
            raise ValueError(f"Project directory does not exist: {root}")
        project_id = re.sub(r"[^a-z0-9_-]+", "-", name.lower()).strip("-_")
        if not project_id:
            project_id = "project-" + os.urandom(8).hex()
        entries = self.entries()
        data_dir = safe_path(self.home / "projects", project_id)
        if project_id in entries or data_dir.exists():
            raise ValueError(f"Project already exists: {project_id}")
        if any(Path(e["root"]).resolve() == root for e in entries.values()):
            raise ValueError("This directory is already registered")
        project = Project(project_id, name, root, data_dir)
        project.data_dir.mkdir(parents=True)
        try:
            project.transcripts_dir.mkdir()
            project.handoffs_dir.mkdir()
            atomic_write(project.project_file, (
                f"# Project\n\n## Name\n\n{name}\n\n## Goal\n\n"
                "Add the project's long-term goal here.\n\n## Requirements\n\n"
                "- Keep changes focused and reuse established solutions.\n"
                "- Verify changes with appropriate tests.\n\n## Architecture\n\n"
                "Record stable architectural decisions here.\n\n## Rules\n\n"
                "Actual files, Git state, and tests outrank handoffs.\n"
                "Re-inspect the repository at the start of each session.\n"
            ))
            save_state(project, Session())
            entries[project_id] = {"name": name, "root": str(root)}
            write_json(self.path, entries)
        except BaseException:
            shutil.rmtree(project.data_dir)
            raise
        return project
