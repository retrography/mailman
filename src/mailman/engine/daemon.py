"""The daemon: keeps an IMAP IDLE connection on the inbox, and on every wake-up (or at the poll interval)
runs the triggers of config/jobs.yaml against what changed in the mailbox.

Triggers:  new_mail (a message arrived in a label) · label_added (a label was put on a message) ·
           start (once per start) · schedule (once a day, during the given hour) ·
           interval (every so many minutes, at the next wake-up)
Everything is logged in data/mailman.db. With dry-run nothing in Gmail changes.
Stop with Ctrl-C or SIGTERM: the email in hand is finished first; a second signal stops at once.
"""

from __future__ import annotations

import asyncio
import logging
import os
import signal
import time
from pathlib import Path

from googleapiclient.errors import HttpError
from imapclient import IMAPClient
from imapclient.exceptions import IMAPClientError

from mailman import gmail
from mailman.engine.core import Engine
from mailman.engine.jobs import Runner
from mailman.store import Store

log = logging.getLogger("mailman")
ROOT = Path(__file__).resolve().parents[3]


def data_dir() -> Path:
    """Where the log, caches, and token live (MAILMAN_DATA; default: data/ in the project)."""
    return Path(os.environ.get("MAILMAN_DATA", ROOT / "data"))


def load_env(path: Path = ROOT / ".env") -> None:
    """KEY=VALUE lines from .env (gitignored) — launchd doesn't load the shell profile."""
    if path.exists():
        for line in path.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


class Daemon:
    def __init__(self, dry_run: bool | None = None, poll: int | None = None):
        self.engine = Engine()
        s = self.engine.config.settings
        self.dry_run = s.get("dry_run", False) if dry_run is None else dry_run
        self.poll = poll or s.get("poll_seconds", 300)
        self.idle_renew = s.get("idle_renew_seconds", 540)
        self.store = Store(data_dir() / "mailman.db")
        self.svc = gmail.service()
        self.runner = Runner(self.engine, self.svc, self.store, data_dir(), self.dry_run)
        self.stopping = False
        self.started = False
        self.last_run: dict[str, float] = {}   # job → when an interval trigger last ran it
        for sig in (signal.SIGINT, signal.SIGTERM):
            signal.signal(sig, self.request_stop)

    def request_stop(self, signum, _frame) -> None:
        if self.stopping:
            raise SystemExit(1)
        self.stopping = True
        log.info("%s received: finishing the current email, then stopping", signal.Signals(signum).name)

    def triggers(self, kind: str) -> list[dict]:
        return [t for t in self.engine.config.jobs["triggers"] if t["on"] == kind]

    def reload_if_changed(self) -> None:
        """A configuration file was edited (by you, the UI, or a job): reload. A half-saved or invalid file
        keeps the last good configuration."""
        cfg = self.engine.config
        try:
            m = cfg.mtime()
        except OSError:
            return
        if m == cfg.loaded_at:
            return
        try:
            self.engine.reload()
        except Exception as e:
            cfg.loaded_at = m
            log.error("configuration NOT reloaded (keeping the previous one): %s", str(e).replace("\n", " "))
            self.store.set("config_error", str(e).replace("\n", " ")[:300])
            return
        self.store.set("config_error", "")
        log.info("configuration changed on disk: reloaded")

    # ------------------------------------------------------------ what changed in the mailbox

    def checkpoint(self) -> str:
        cp = self.store.get("history_id")
        if not cp:   # first start: begin from now, don't process the backlog
            cp = gmail.history_id(self.svc)
            self.store.set("history_id", cp)
            log.info("first start: checkpoint %s (existing mail is left alone)", cp)
        return cp

    def changes(self) -> tuple[list[dict], list[dict], str]:
        start = self.checkpoint()
        try:
            return gmail.changes_since(self.svc, start)
        except HttpError as e:
            if e.status_code != 404:
                raise
            latest = gmail.history_id(self.svc)   # checkpoint too old: restart from now
            log.warning("checkpoint %s expired; restarting from %s (mail in between is not processed)", start, latest)
            return [], [], latest

    def process(self) -> None:
        self.store.set("heartbeat", str(int(time.time())))
        self.reload_if_changed()
        added, labelled, latest = self.changes()
        labels = self.runner.labels

        # label_added triggers (e.g. you blocked something): list and maintenance jobs, never fatal
        for t in self.triggers("label_added"):
            lid, own = labels.id(t["label"]), self.runner.own.get(t["label"], set())
            hit = [m["id"] for m in [*added, *labelled] if lid in m["labels"] and m["id"] not in own]
            if hit:
                try:
                    self.run_job(t["run"])
                except Exception as e:
                    log.warning("%s failed: %s", t["run"], e)

        for t in self.triggers("schedule"):
            try:
                self.scheduled(t)
            except Exception as e:
                log.warning("%s failed: %s", t["run"], e)

        # new_mail triggers: the rules, one email at a time
        work: list[tuple[str, dict]] = []
        retries: set[str] = set()   # failed earlier: if the first of these fails again, the rest wait
        days = self.engine.config.settings.get("retry_failed_days", 7)
        due = [t for t in self.triggers("interval") if time.time() - self.last_run.get(t["run"], 0) >= t["minutes"] * 60]
        for t in ([] if self.started else self.triggers("start")) + due:
            self.last_run[t["run"]] = time.time()
            job = self.runner.job(t["run"])
            if job.get("ids_from") == "failed":
                work += [(i, job) for i in self.store.failed(days) if i not in retries]
                retries = {i for i, _ in work}
        self.started = True
        seen = {i for i, _ in work}
        for t in self.triggers("new_mail"):
            lid, job = labels.id(t["label"]), self.runner.job(t["run"])
            for m in added:
                if lid in m["labels"] and m["id"] not in seen and not self.store.handled(m["id"]):
                    seen.add(m["id"])
                    work.append((m["id"], job))
        if not work:
            self.store.set("history_id", latest)
            return

        async def run():
            async with self.engine.jev.client() as client:
                still_failing = False
                for msg_id, job in work:   # sequential: a handful at a time, keeps the log ordered
                    if self.stopping:      # the rest is picked up after a restart (checkpoint not advanced)
                        break
                    if still_failing and msg_id in retries:
                        continue
                    if await self.runner.classify(client, msg_id, job) is False and msg_id in retries:
                        still_failing = True

        asyncio.run(run())
        if not self.stopping:
            self.store.set("history_id", latest)

    def run_job(self, name: str, params: dict | None = None):
        job = self.runner.job(name)
        if "sync_list" in job:
            return self.runner.sync_list(job["sync_list"])
        if "search" in job and "outcome" in job:
            return self.runner.run_search(name, params)
        raise ValueError(f"job {name!r} cannot be run from a trigger")

    def scheduled(self, t: dict) -> None:
        """Once a day, during the trigger's hour. If the daemon isn't running then, it waits for the next day."""
        job = self.runner.job(t["run"])
        today, key = time.strftime("%Y-%m-%d"), f"last_run:{t['run']}"
        if not job.get("enabled", True) or time.localtime().tm_hour != t["hour"] or self.store.get(key) == today:
            return
        self.run_job(t["run"])
        self.store.set(key, today)

    # ------------------------------------------------------------ IMAP IDLE loop

    def imap(self) -> IMAPClient:
        creds = gmail.credentials()
        server = IMAPClient("imap.gmail.com", ssl=True, timeout=self.idle_renew + 60)
        server.oauth2_login(self.engine.config.settings["mailbox"], creds.token)
        server.select_folder("INBOX", readonly=True)
        return server

    @staticmethod
    def start_idle(server: IMAPClient) -> bool:
        """Enter IDLE. Mail can arrive between two commands ('* 86 EXISTS'); imapclient then mistakes that
        notice for the reply to IDLE. Consume it with a NOOP and try again. Returns True if mail arrived."""
        for attempt in range(3):
            try:
                server.idle()
                return False
            except IMAPClientError as e:
                if "unexpected response" not in str(e) or attempt == 2:
                    raise
                server.noop()
        return True

    def run(self) -> None:
        log.info("mailman daemon started (%s, poll every %ss)", "DRY RUN" if self.dry_run else "LIVE", self.poll)
        backoff = 5
        while not self.stopping:
            server = None
            connected_at = time.time()
            try:
                server = self.imap()
                connected_at = time.time()
                self.process()   # catch up on anything since the checkpoint
                self.store.set("gmail_error", "")
                while not self.stopping:
                    if self.start_idle(server):   # mail arrived just before IDLE: handle it first
                        self.process()
                        continue
                    deadline = time.time() + min(self.poll, self.idle_renew)
                    events = []
                    while time.time() < deadline and not events and not self.stopping:
                        events = server.idle_check(timeout=min(5, max(1, int(deadline - time.time()))))
                    self.store.set("heartbeat", str(int(time.time())))
                    server.idle_done()
                    if self.stopping:
                        break
                    self.process()
                    try:   # long processing can outlive the IMAP connection
                        server.noop()
                    except Exception:
                        log.info("IMAP connection closed while processing; reconnecting")
                        break
            except Exception as e:
                if self.stopping:
                    break
                if server is not None and time.time() - connected_at > 60:
                    # Gmail closes long-lived IMAP connections (and sleep / network changes do too): expected.
                    log.info("IMAP connection closed (%s); reconnecting", e)
                    backoff = 5
                    continue
                log.warning("connection problem (%s); reconnecting in %ss", e, backoff)
                self.store.set("gmail_error", f"{int(time.time())} {str(e)[:300]}")
                for _ in range(backoff):
                    if self.stopping:
                        break
                    time.sleep(1)
                backoff = min(backoff * 2, 300)
            finally:
                if server is not None:
                    try:
                        server.logout()
                    except Exception:
                        pass
        log.info("stopped")


def main(dry_run: bool | None, poll: int | None) -> None:
    load_env()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    for noisy in ("httpx", "httpx2", "typesafe_sdk", "googleapiclient.discovery_cache", "googleapiclient.http"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    Daemon(dry_run=dry_run, poll=poll).run()
