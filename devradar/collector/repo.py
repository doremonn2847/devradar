"""Map a session's cwd to a project key, folding git worktrees into their main repo."""
from __future__ import annotations

import functools
import os
import subprocess
from pathlib import PureWindowsPath, PurePosixPath


def _norm(p: str) -> str:
    return os.path.normcase(os.path.normpath(p))


def _name(p: str) -> str:
    # Session files may carry Windows paths even when read elsewhere; take the last part either way.
    parts = PureWindowsPath(p).parts if "\\" in p else PurePosixPath(p).parts
    return parts[-1] if parts else p


def _git(cwd: str, *args: str) -> str | None:
    try:
        r = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=5,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except Exception:
        return None
    return r.stdout if r.returncode == 0 else None


def _nearest_existing_dir(p: str) -> str | None:
    p = os.path.normpath(p)
    while p and not os.path.isdir(p):
        parent = os.path.dirname(p)
        if parent == p:
            return None
        p = parent
    return p or None


@functools.lru_cache(maxsize=512)
def resolve_project(cwd: str | None) -> tuple[str, str]:
    """Return (project_key, display_name). Never raises."""
    if not cwd:
        return ("unknown", "unknown")
    root = None
    probe = _nearest_existing_dir(cwd)  # agents often work in scratch subfolders that are later deleted
    if probe:
        out = _git(probe, "worktree", "list", "--porcelain")
        if out:
            for line in out.splitlines():
                if line.startswith("worktree "):
                    root = line[len("worktree "):].strip()
                    break
        if root is None:
            top = _git(probe, "rev-parse", "--show-toplevel")
            root = top.strip() if top else None
    root = root or cwd
    return (_norm(root), _name(os.path.normpath(root)))
