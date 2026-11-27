"""Incremental reading of append-only JSONL files."""
from __future__ import annotations

from pathlib import Path


def read_new_lines(path: Path, offset: int) -> tuple[list[str], int]:
    """Return complete lines after ``offset`` and the offset just past the last one.

    A trailing partial line (still being written) is left for the next call.
    If the file shrank below ``offset`` (rewritten/rotated), reading restarts at 0.
    """
    path = Path(path)
    size = path.stat().st_size
    if size < offset:
        offset = 0
    with open(path, "rb") as f:
        f.seek(offset)
        data = f.read()
    end = data.rfind(b"\n")
    if end < 0:
        return [], offset
    chunk = data[: end + 1]
    lines = [ln.decode("utf-8", errors="replace").rstrip("\r") for ln in chunk.split(b"\n")[:-1]]
    return [ln for ln in lines if ln.strip()], offset + end + 1
