"""Bounded repository inspection and checkpoint comparison."""

import hashlib
import subprocess
from pathlib import Path

from .models import Project
from .storage import preview, read_preview, safe_path


def inspect_project(project: Project):
    entries = sorted(p.name + ("/" if p.is_dir() else "") for p in project.root.iterdir())
    parts = ["Directory entries:\n" + preview("\n".join(entries), 2000)]
    for args in (["rev-parse", "--show-toplevel"], ["status", "--short"],
                 ["diff", "--stat"], ["diff", "--cached", "--stat"]):
        try:
            result = subprocess.run(
                ["git", "-C", str(project.root), *args, *([] if args[0] == "rev-parse" else ["--", "."])], capture_output=True,
                text=True, errors="replace", timeout=10, check=False,
            )
            text = result.stdout + result.stderr
            if args[0] == "rev-parse":
                if result.returncode:
                    parts.append("Git repository: unavailable; no project-local repository detected.")
                    break
                git_root = Path(result.stdout.strip()).resolve()
                scope = "project-local" if git_root == project.root.resolve() else "ancestor repository"
                parts.append(f"Git root: {git_root} ({scope}). "
                             "Repository initialization and commits require a user request or project requirement.")
                continue
        except (OSError, subprocess.TimeoutExpired) as exc:
            text = f"Git inspection unavailable: {exc}"
        parts.append(f"git {' '.join(args)}:\n{preview(text, 2000) or '(clean)'}")
    for name in ("AGENTS.md", "README.md", "pyproject.toml", "package.json", "go.mod", "Cargo.toml"):
        try:
            path = safe_path(project.root, name)
            if path.is_file():
                parts.append(f"{name}:\n{read_preview(path, 1500)}")
        except (OSError, ValueError) as exc:
            parts.append(f"{name}: inspection unavailable ({exc})")
    return "\n\n".join(parts)


def repository_snapshot(project: Project, changed_files=()):
    """Capture bounded Git facts and hashes for files relevant to this session."""
    def git(*args):
        return subprocess.run(["git", "-C", str(project.root), *args],
                              capture_output=True, timeout=10, check=False)

    try:
        root = git("rev-parse", "--show-toplevel")
        if root.returncode:
            return {"available": False, "reason": "Git repository unavailable"}
        head = git("rev-parse", "HEAD")
        status = git("status", "--porcelain=v1", "-z", "--untracked-files=normal", "--", ".")
        if status.returncode:
            return {"available": False, "reason": "Git status unavailable"}
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"available": False, "reason": str(exc)}
    paths = set(changed_files)
    records = status.stdout.split(b"\0")
    index = 0
    while index < len(records) and records[index]:
        record = records[index]
        paths.add(record[3:].decode("utf-8", errors="replace"))
        index += 2 if record[:2].strip()[:1] in (b"R", b"C") else 1
    hashes = {}
    for relative in sorted(paths)[:100]:
        try:
            path = safe_path(project.root, relative)
            if not path.is_file():
                hashes[relative] = "missing"
            elif path.stat().st_size > 4 * 1024 * 1024:
                hashes[relative] = "too-large-to-hash"
            else:
                digest = hashlib.sha256()
                with path.open("rb") as handle:
                    for chunk in iter(lambda: handle.read(65536), b""):
                        digest.update(chunk)
                hashes[relative] = digest.hexdigest()
        except (OSError, ValueError):
            hashes[relative] = "unavailable"
    return {"available": True, "git_root": root.stdout.decode(errors="replace").strip(),
            "head": head.stdout.decode(errors="replace").strip() if not head.returncode else None,
            "status": status.stdout.decode(errors="replace")[:8000],
            "status_digest": hashlib.sha256(status.stdout).hexdigest(),
            "file_hashes": hashes, "paths_truncated": len(paths) > 100}


def verify_checkpoint(project: Project, checkpoint):
    expected = checkpoint.get("repository", {})
    if not expected.get("available"):
        return "Git comparison unavailable in the previous session; inspect files directly."
    current = repository_snapshot(project, checkpoint.get("changed_files", []))
    if not current.get("available"):
        return "Git comparison unavailable now; inspect files directly."
    differences = [key for key in ("git_root", "head", "status_digest", "file_hashes")
                   if current[key] != expected.get(key)]
    if differences:
        return "Repository changed since the handoff (" + ", ".join(differences) + "). Inspect before continuing."
    if current["paths_truncated"] or "too-large-to-hash" in current["file_hashes"].values():
        return "Git HEAD and status match; some files were too large or numerous to hash. Inspect them directly."
    return "Git HEAD, status, and recorded file hashes match the handoff checkpoint."
