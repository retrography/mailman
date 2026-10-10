"""Lookups: how many messages a search finds in the mailbox, cached in SQLite (table lookup_cache).

`cache` is a list of cases, first match wins: {min: 2, ttl: 30d} caches a count of 2 or more for 30 days;
{ttl: 1d} caches anything else for a day; `ttl: forever`; no matching case → not cached.
"""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path

from mailman import mailbox

UNITS = {"s": 1, "m": 60, "h": 3600, "d": 86400}
SCHEMA = ("CREATE TABLE IF NOT EXISTS lookup_cache (fact TEXT, query TEXT, exclude TEXT, value INTEGER, ts REAL, "
          "PRIMARY KEY (fact, query, exclude))")


def seconds(ttl) -> float:
    if ttl == "forever":
        return float("inf")
    return float(ttl[:-1]) * UNITS[ttl[-1]] if isinstance(ttl, str) else float(ttl)


def ttl_for(value: int, cases: list[dict]) -> float | None:
    for c in cases:
        if "min" not in c or value >= c["min"]:
            return seconds(c["ttl"])
    return None


class MailboxLookups:
    def __init__(self, box, db: Path):
        self.box = box
        self.db = sqlite3.connect(db, check_same_thread=False)
        self.db.execute(SCHEMA)

    def count(self, fact: str, query: str, limit: int, exclude: str | None, cache: list[dict]) -> int:
        """Messages matching `query`, up to `limit`, not counting the message `exclude`."""
        key = (mailbox.key(fact, self.box.name), query, "" if cache_ignores_exclude(cache) else (exclude or ""))
        row = self.db.execute("SELECT value, ts FROM lookup_cache WHERE fact = ? AND query = ? AND exclude = ?",
                              key).fetchone()
        if row:
            ttl = ttl_for(row[0], cache)
            if ttl is not None and time.time() - row[1] < ttl:
                return row[0]
        n = self.box.count(query, limit, exclude)
        if ttl_for(n, cache) is not None:
            self.db.execute("INSERT OR REPLACE INTO lookup_cache VALUES (?, ?, ?, ?, ?)", (*key, n, time.time()))
            self.db.commit()
        return n


def cache_ignores_exclude(cache: list[dict]) -> bool:
    """A count cached only when positive holds for any message from the sender, not just this one."""
    return bool(cache) and all("min" in c and c["min"] >= 1 for c in cache)


class FixedLookups:
    """Lookup values given up front, per fact name (tests and the equivalence check)."""

    def __init__(self, values: dict[str, int]):
        self.values = values

    def count(self, fact: str, query: str, limit: int, exclude: str | None, cache: list[dict]) -> int:
        return self.values[fact]
