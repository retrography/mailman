"""SQLite log of every email the daemons handled (one per mailbox), plus their checkpoints (DESIGN.md §3)."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS actions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ts          TEXT NOT NULL,          -- UTC, ISO 8601
    gmail_id    TEXT NOT NULL,
    thread_id   TEXT,
    sender      TEXT,
    subject     TEXT,
    decision    TEXT,                   -- keep / delete / spam / block
    rule        TEXT,                   -- the rule that decided ('' = no rule matched)
    action      TEXT,                   -- what was done: none / trash / spam / block / dry-run:<action> / error
    dry_run     INTEGER NOT NULL,
    error       TEXT,
    tokens      INTEGER,
    facts       TEXT,                   -- JSON: the facts the rules saw
    jev         TEXT,                   -- JSON: Jev's answers
    account     TEXT NOT NULL DEFAULT 'gmail'   -- the mailbox it is in (gmail_id is its id there)
);
CREATE INDEX IF NOT EXISTS actions_gmail_id ON actions (gmail_id);
CREATE TABLE IF NOT EXISTS state (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS events (id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL, kind TEXT, detail TEXT);
"""


class Store:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, check_same_thread=False)   # the web server calls from worker threads
        self.db.executescript(SCHEMA)
        if "account" not in [c[1] for c in self.db.execute("PRAGMA table_info(actions)")]:   # a log from before 0.6
            try:
                self.db.execute("ALTER TABLE actions ADD COLUMN account TEXT NOT NULL DEFAULT 'gmail'")
            except sqlite3.OperationalError:   # another process added it in the same moment
                pass

    def get(self, key: str) -> str | None:
        row = self.db.execute("SELECT value FROM state WHERE key = ?", (key,)).fetchone()
        return row[0] if row else None

    def set(self, key: str, value: str) -> None:
        self.db.execute("INSERT INTO state (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = ?",
                        (key, value, value))
        self.db.commit()

    def event(self, kind: str, detail: str = "") -> None:
        """Something other than an email: a configuration save, an undo, a job run."""
        self.db.execute("INSERT INTO events (ts, kind, detail) VALUES (?, ?, ?)",
                        (datetime.now(timezone.utc).isoformat(timespec="seconds"), kind, detail))
        self.db.commit()

    def handled(self, gmail_id: str) -> bool:
        """Already acted on (a dry-run entry does not count, so a later real run still acts)."""
        return self.db.execute("SELECT 1 FROM actions WHERE gmail_id = ? AND dry_run = 0 AND error IS NULL",
                               (gmail_id,)).fetchone() is not None

    def failed(self, days: int = 7, account: str = "gmail") -> list[str]:
        """Emails of a mailbox from the last `days` whose attempts all failed (no successful live or dry-run entry)."""
        return [r[0] for r in self.db.execute(
            "SELECT gmail_id FROM actions WHERE ts >= datetime('now', ?) AND account = ? GROUP BY gmail_id "
            "HAVING SUM(error IS NULL) = 0 ORDER BY MIN(id)", (f"-{days} days", account))]

    def log(self, *, gmail_id: str, thread_id: str = "", sender: str = "", subject: str = "", decision: str = "",
            rule: str = "", action: str = "", dry_run: bool, error: str | None = None, tokens: int = 0,
            facts: dict | None = None, jev: dict | None = None, account: str = "gmail") -> None:
        self.db.execute(
            "INSERT INTO actions (ts, gmail_id, thread_id, sender, subject, decision, rule, action, dry_run, "
            "error, tokens, facts, jev, account) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (datetime.now(timezone.utc).isoformat(timespec="seconds"), gmail_id, thread_id, sender, subject,
             decision, rule, action, int(dry_run), error, tokens,
             json.dumps(facts or {}, ensure_ascii=False, default=str), json.dumps(jev or {}, ensure_ascii=False), account))
        self.db.commit()
