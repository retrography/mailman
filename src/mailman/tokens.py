"""The mailbox sign-ins, all in one file (MAILMAN_TOKENS; default: tokens.json in the data folder):

  {"gmail":   Google's authorized-user token, plus signed_in,
   "outlook": {client_id, tenant, refresh_token, signed_in}}

signed_in is when that sign-in was made; it changes when you sign in again. Whoever has this file can read the
mailboxes: it is written 0600 and never part of an export. The web interface and the daemons write to it at the
same time, so each write takes a lock and replaces the file whole.
"""

from __future__ import annotations

import fcntl
import json
import os
from contextlib import contextmanager
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OLD = {"gmail": "token.json", "outlook": "token-outlook.json"}   # one file per mailbox, before 0.11.0


def path() -> Path:
    return Path(os.environ.get("MAILMAN_TOKENS") or Path(os.environ.get("MAILMAN_DATA", ROOT / "data")) / "tokens.json")


def _read(p: Path) -> dict:
    try:
        return json.loads(p.read_text())
    except FileNotFoundError:
        return {}


def load(account: str) -> dict | None:
    """The account's sign-in; None without one."""
    return _read(path()).get(account)


@contextmanager
def _all(p: Path):
    """Every sign-in in `p`, to change in place; written back when the block ends."""
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p.with_name(p.name + ".lock"), "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        everything = _read(p)
        yield everything
        tmp = p.with_name(p.name + ".tmp")
        with open(os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w") as f:
            json.dump(everything, f, indent=1)
        os.chmod(tmp, 0o600)
        tmp.replace(p)


def save(account: str, entry: dict) -> None:
    """Store one mailbox's sign-in, keeping the others."""
    with _all(path()) as everything:
        everything[account] = entry


def adopt(folder: Path, into: Path) -> list[str]:
    """Take the per-mailbox files of earlier versions found in `folder` into `into`, then remove them. A sign-in
    already in `into` is kept. The mailboxes taken in."""
    found = {a: folder / name for a, name in OLD.items() if (folder / name).exists()}
    if not found:
        return []
    with _all(into) as everything:
        for account, f in found.items():
            if account not in everything:
                entry = json.loads(f.read_text())
                entry.setdefault("signed_in", f.stat().st_mtime)   # Gmail's file had its time as the file's own
                everything[account] = entry
    for f in found.values():
        f.unlink()
    return sorted(found)
