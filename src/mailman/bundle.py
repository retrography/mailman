"""An installation as one file: the configuration, and optionally the log and the test set. Never the Gmail
sign-in. Made by `mailman export` or the interface's Backup page; unpacked by the app at start (mailman.ha)."""

from __future__ import annotations

import sqlite3
import tempfile
import zipfile
from pathlib import Path


def write(out: Path, config: Path, data: Path, everything: bool) -> Path:
    """`everything` adds the log (history, undo, what is known about senders) and the test set (stored emails
    with the classifier's answers, which test-before-save runs on) — both hold personal mail."""
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for f in sorted(config.glob("*.yaml")):
            z.write(f, f"config/{f.name}")
        if not everything:
            return out
        if (data / "mailman.db").exists():   # a consistent copy, also while the daemon is running
            with tempfile.TemporaryDirectory() as tmp:
                copy = sqlite3.connect(Path(tmp) / "mailman.db")
                with sqlite3.connect(data / "mailman.db") as live:
                    live.backup(copy)
                copy.close()
                z.write(Path(tmp) / "mailman.db", "data/mailman.db")
        for sub in ("cache/sample", "cache/probe", "testset"):
            for f in sorted((data / sub).rglob("*")) if (data / sub).exists() else []:
                if f.is_file():
                    z.write(f, f"data/{f.relative_to(data)}")
        if (data / "cache/sample_index.json").exists():
            z.write(data / "cache/sample_index.json", "data/cache/sample_index.json")
    return out
