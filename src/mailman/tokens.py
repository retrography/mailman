"""The mailbox sign-ins, all in one file (MAILMAN_TOKENS; default: tokens.json in the data folder):

  {"gmail":   Google's authorized-user token, plus signed_in,
   "outlook": {client_id, tenant, refresh_token, signed_in}}

signed_in is when that sign-in was made; it changes when you sign in again. Whoever has this file can read the
mailboxes: it is written 0600 and never part of an export.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from mailman import jsonfile

ROOT = Path(__file__).resolve().parents[2]
OLD = {"gmail": "token.json", "outlook": "token-outlook.json"}   # one file per mailbox, before 0.11.0


def path() -> Path:
    return Path(os.environ.get("MAILMAN_TOKENS") or Path(os.environ.get("MAILMAN_DATA", ROOT / "data")) / "tokens.json")


def load(account: str) -> dict | None:
    """The account's sign-in; None without one."""
    return jsonfile.read(path()).get(account)


def save(account: str, entry: dict) -> None:
    """Store one mailbox's sign-in, keeping the others."""
    with jsonfile.edit(path(), 0o600) as everything:
        everything[account] = entry


def adopt(folder: Path, into: Path) -> list[str]:
    """Take the per-mailbox files of earlier versions found in `folder` into `into`, then remove them. A sign-in
    already in `into` is kept. The mailboxes taken in."""
    found = {a: folder / name for a, name in OLD.items() if (folder / name).exists()}
    if not found:
        return []
    with jsonfile.edit(into, 0o600) as everything:
        for account, f in found.items():
            if account not in everything:
                entry = json.loads(f.read_text())
                entry.setdefault("signed_in", f.stat().st_mtime)   # Gmail's file had its time as the file's own
                everything[account] = entry
    for f in found.values():
        f.unlink()
    return sorted(found)
