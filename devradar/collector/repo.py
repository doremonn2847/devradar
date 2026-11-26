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
                           encoding="utf-8", timeout=5)
    except (OSError, subprocess.SubprocessError):
        return None
    return r.stdout if r.returncode == 0 else None


@functools.lru_cache(maxsize=512)
def resolve_project(cwd: str | None) -> tuple[str, str]:
    """Return (project_key, display_name). Never raises."""
    if not cwd:
        return ("unknown", "unknown")
    root = None
    if os.path.isdir(cwd):
        out = _git(cwd, "worktree", "list", "--porcelain")
        if out:
            for line in out.splitlines():
                if line.startswith("worktree "):
                    root = line[len("worktree "):].strip()
                    break
        if root is None:
            top = _git(cwd, "rev-parse", "--show-toplevel")
            root = top.strip() if top else None
    root = root or cwd
    return (_norm(root), _name(os.path.normpath(root)))
