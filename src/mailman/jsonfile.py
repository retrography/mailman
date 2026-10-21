"""A JSON file that several processes change (the web interface and one daemon per mailbox): each change takes a
lock, and the file is replaced whole, so a reader never sees half of it."""

from __future__ import annotations

import fcntl
import json
import os
from contextlib import contextmanager
from pathlib import Path


def read(p: Path) -> dict:
    try:
        return json.loads(p.read_text())
    except FileNotFoundError:
        return {}


@contextmanager
def edit(p: Path, mode: int = 0o644):
    """The file's contents, to change in place; written back when the block ends."""
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p.with_name(p.name + ".lock"), "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        everything = read(p)
        yield everything
        tmp = p.with_name(p.name + ".tmp")
        with open(os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode), "w") as f:
            json.dump(everything, f, indent=1, ensure_ascii=False)
        os.chmod(tmp, mode)
        tmp.replace(p)
