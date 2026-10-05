"""Jobs (config/jobs.yaml): everything the app does besides deciding about one email.

A job is one of:
  rules: true                 apply the rules to messages (new mail, a retry, or a label clean-up)
  sync_list: <list>           fill a list from its `source` (the senders of the mail in a Gmail label),
                              normalise it, save it, and run the list's `on_new_entry` job per new entry
  search + outcome            perform an outcome on every message a Gmail search finds
Options: enabled, only_if_in (a label the message must still carry), include_trash (the search also looks
in Trash and Spam), log {decision, rule}, manual [params].
`{name}` in a search or a log text is a parameter of the run (e.g. {entry}, {label}).
"""

from __future__ import annotations

import json
import logging
import time
from email.utils import parseaddr
from pathlib import Path

from googleapiclient.errors import HttpError

from mailman import gmail
from mailman.engine import lists as elists
from mailman.engine import outcomes
from mailman.engine.core import Engine
from mailman.engine.lookups import GmailLookups
from mailman.store import Store

log = logging.getLogger("mailman")


class Runner:
    def __init__(self, engine: Engine, svc, store: Store, data: Path, dry_run: bool = False):
        self.engine, self.svc, self.store, self.data, self.dry_run = engine, svc, store, data, dry_run
        self.labels = outcomes.Labels(svc)
        self.lookups = GmailLookups(svc, data / "mailman.db")
        self.own: dict[str, set[str]] = {}   # label name → ids this app labelled (their label events are ours)

    def job(self, name: str) -> dict:
        return self.engine.config.jobs["jobs"][name]

    # ------------------------------------------------------------ one email through the rules

    async def classify(self, client, msg_id: str, job: dict) -> bool | None:
        """One email through the rules. True: done; False: it failed (logged, retried later); None: skipped."""
        try:
            raw = gmail.get(self.svc, msg_id)
        except HttpError as e:
            if e.status_code == 404:   # deleted before we got to it
                return None
            raise
        email = self.engine.parse(raw)
        msg = email.msg
        if job.get("only_if_in") and job["only_if_in"] not in msg.label_ids:   # moved meanwhile
            if self.store.db.execute("SELECT 1 FROM actions WHERE gmail_id = ? AND error IS NOT NULL", (msg.id,)).fetchone() \
                    and not self.store.handled(msg.id):   # it failed earlier and you dealt with it yourself: no longer waiting
                self.store.log(gmail_id=msg.id, thread_id=msg.thread_id, sender=msg.h("from"), subject=msg.h("subject"),
                               rule=f"left {job['only_if_in']} before it could be handled", action="none", dry_run=self.dry_run)
            return None
        meta = {"gmail_id": msg.id, "thread_id": msg.thread_id, "sender": msg.h("from"), "subject": msg.h("subject")}
        try:
            c = await self.engine.classify(client, email, self.lookups)
        except Exception as e:
            log.error("classify %s failed: %s", msg.id, e)
            self.store.log(**meta, action="error", dry_run=self.dry_run, error=f"classify: {e}")
            self.store.set("classifier_error", f"{int(time.time())} {str(e)[:300]}")
            return False
        if c["tokens"]:   # the classifier answered: whatever was wrong is over
            self.store.set("classifier_error", "")
        rest = {"decision": c["decision"], "rule": c["rule"], "dry_run": self.dry_run, "tokens": c["tokens"],
                "facts": c["facts"], "jev": c["jev"]}
        spec = self.engine.config.outcomes[c["decision"]]
        try:
            acted = self.perform(spec, c["labels"], [msg.id])
        except Exception as e:
            log.error("%s %s failed: %s", c["decision"], msg.id, e)
            self.store.log(**meta, **rest, action="error", error=f"{c['decision']}: {e}")
            return False
        action = outcomes.action_name(c["decision"], spec, c["labels"], acted)
        if self.dry_run and acted:
            action = f"dry-run:{action}"
        self.store.log(**meta, **rest, action=action)
        log.info("%-8s %-22s %s | %s", "keep" if action == "none" else action, c["rule"].split(" (")[0] or "-",
                 msg.h("from")[:40], msg.h("subject")[:60])
        return True

    def perform(self, spec: dict, labels: list[str], ids: list[str], batch: bool = False) -> bool:
        """Do an outcome (or, in a dry run, only say whether it would do anything)."""
        trash, add, remove = outcomes.plan(spec, labels)
        if self.dry_run or not ids:
            return bool(ids) and bool(trash or add or remove)
        acted = outcomes.apply(self.svc, self.labels, spec, labels, ids, batch=batch)
        for name in add:
            self.own.setdefault(name, set()).update(ids)
        return acted

    # ------------------------------------------------------------ search + outcome

    def run_search(self, name: str, params: dict | None = None) -> list[str]:
        job = self.job(name)
        if not job.get("enabled", True):
            return []
        params = params or {}
        ids = gmail.search(self.svc, job["search"].format(**params), include_trash=job.get("include_trash", False))
        spec = self.engine.config.outcomes[job["outcome"]]
        failed: dict[str, str] = {}
        try:
            self.perform(spec, [], ids, batch=True)
        except HttpError:   # a batch failed: one by one, skipping what cannot be changed
            for i in ids:
                try:
                    self.perform(spec, [], [i])
                except HttpError as e:
                    failed[i] = str(e)
        word = ("dry-run:" if self.dry_run else "") + spec.get("log_as", job["outcome"])
        entry = job.get("log", {})
        for i in ids:
            self.store.log(gmail_id=i, sender=str(params.get("entry", "")), decision=entry.get("decision", ""),
                           rule=entry.get("rule", name).format(**params), dry_run=self.dry_run,
                           action="error" if i in failed else word, error=failed.get(i))
        if ids:
            log.info("%s %s: %d messages%s", name, " ".join(map(str, params.values())), len(ids),
                     f" ({len(failed)} failed)" if failed else "")
        return ids

    # ------------------------------------------------------------ the rules over mail already filed

    async def classify_search(self, name: str, params: dict, pace: float = 0.3) -> dict:
        """Step 1 of a manual rules job (e.g. clean_label): classify every message its search finds into
        data/cache/<job>_<params>.json. Nothing in Gmail changes. Can be interrupted and resumed."""
        import asyncio
        import time

        job = self.job(name)
        out = self.data / "cache" / f"{name}_{'_'.join(str(v) for v in params.values() if isinstance(v, str))}.json"
        ids = gmail.search(self.svc, job["search"].format(**params))[:params.get("limit") or None]   # newest first
        done = json.loads(out.read_text()) if out.exists() else {}
        todo = [i for i in ids if i not in done or "error" in done[i]]   # earlier failures are tried again
        sem = asyncio.Semaphore(self.engine.config.settings["jev"].get("concurrency", 8))
        async with self.engine.jev.client() as client:
            for start in range(0, len(todo), 30):
                emails = []
                for i in todo[start:start + 30]:   # the Gmail client is used one call at a time
                    emails.append(self.engine.parse(self.fetch(i)))
                    time.sleep(pace)
                results = await asyncio.gather(*(self.engine.classify(client, e, self.lookups, sem) for e in emails),
                                               return_exceptions=True)
                for e, r in zip(emails, results):
                    done[e.msg.id] = {"error": str(r)[:200]} if isinstance(r, Exception) else {
                        "sender": e.msg.h("from"), "subject": e.msg.h("subject"), "thread": e.msg.thread_id,
                        "decision": r["decision"], "rule": r["rule"], "labels": r["labels"],
                        "tokens": r["tokens"], "facts": r["facts"], "jev": r["jev"]}
                out.parent.mkdir(parents=True, exist_ok=True)
                out.write_text(json.dumps(done, ensure_ascii=False, default=str))
                log.info("classified %d/%d", min(start + 30, len(todo)), len(todo))
                if results and all(isinstance(r, Exception) for r in results):   # the classifier is down: stop here
                    raise RuntimeError(f"stopped, every email of the last batch failed: {str(results[0])[:200]}")
        return {i: done[i] for i in ids if i in done}

    def fetch(self, msg_id: str) -> dict:
        """gmail.get, waiting out the per-minute quota."""
        import time

        for attempt in range(10):
            try:
                return gmail.get(self.svc, msg_id)
            except HttpError as e:
                if e.status_code not in (403, 429) or attempt == 9:
                    raise
                time.sleep(30)
        raise RuntimeError("unreachable")

    def apply_classified(self, name: str, params: dict, done: dict) -> dict:
        """Step 2: perform the outcomes of a classified set, one Gmail call per distinct result. With the
        `relabel` parameter the scanned label is taken off the mail the rules no longer give it to."""
        job = self.job(name)
        label, relabel = params.get("label"), params.get("relabel")
        groups: dict[tuple, list[str]] = {}
        for i, r in done.items():
            if "error" in r or r.get("applied"):
                continue
            drop = bool(relabel and label and r["decision"] in ("keep", "file") and label not in r["labels"])
            groups.setdefault((r["decision"], tuple(r["labels"]), drop), []).append(i)
        summary: dict[str, int] = {}
        for (decision, names, drop), ids in groups.items():
            spec = dict(self.engine.config.outcomes[decision])
            ops = list(spec.get("do", []))
            if job.get("never_move_to_inbox"):
                ops = [o for o in ops if not (isinstance(o, dict) and "INBOX" in o.get("add_labels", []))]
            spec["do"] = ops + ([{"remove_labels": [label]}] if drop else [])
            action, error = "none", None
            try:
                acted = self.perform(spec, list(names), ids, batch=True)
                action = outcomes.action_name(decision, spec, list(names), acted) + (f" (-{label})" if drop else "")
                if self.dry_run and acted:
                    action = f"dry-run:{action}"
            except HttpError as e:
                action, error = "error", str(e)[:200]
            summary[action] = summary.get(action, 0) + len(ids)
            for i in ids:
                r = done[i]
                self.store.log(gmail_id=i, thread_id=r["thread"], sender=r["sender"], subject=r["subject"],
                               decision=decision, rule=f"retro:{label or name}: {r['rule']}", action=action,
                               dry_run=self.dry_run, error=error, tokens=r["tokens"], facts=r["facts"], jev=r["jev"])
                if error is None and not self.dry_run:
                    r["applied"] = action
        out = self.data / "cache" / f"{name}_{'_'.join(str(v) for v in params.values() if isinstance(v, str))}.json"
        stored = json.loads(out.read_text()) if out.exists() else {}
        stored.update(done)
        out.write_text(json.dumps(stored, ensure_ascii=False, default=str))
        return summary

    # ------------------------------------------------------------ lists

    def label_senders(self, label: str) -> set[str]:
        """Every sender address in a Gmail label (headers only, remembered per message)."""
        M = self.svc.users().messages()
        lid = self.labels.id(label)
        ids, tok = [], None
        while True:
            res = gmail._execute(M.list(userId="me", labelIds=[lid], maxResults=500, includeSpamTrash=True,
                                        pageToken=tok))
            ids += [m["id"] for m in res.get("messages", [])]
            tok = res.get("nextPageToken")
            if not tok:
                break
        cache = self.data / "cache" / f"{label.lower()}_index.json"
        index = json.loads(cache.read_text()) if cache.exists() else {}
        for mid in ids:
            if mid not in index:
                meta = gmail._execute(M.get(userId="me", id=mid, format="metadata", metadataHeaders=["From", "Subject"]))
                h = {x["name"]: x["value"] for x in meta["payload"].get("headers", [])}
                index[mid] = {"from": parseaddr(h.get("From", ""))[1].lower(), "subject": h.get("Subject", "")}
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps(index, indent=1, ensure_ascii=False))
        return {index[mid]["from"] for mid in ids if index[mid]["from"]}   # only mail in the label now

    def sync_list(self, name: str) -> dict:
        """Add the label's new senders to the list, normalise, save; then the list's on_new_entry job."""
        lists = self.engine.lists
        spec = self.engine.config.lists[name]
        current = lists.values(name)
        new = self.label_senders(spec["source"]["gmail_label"]) - {e.lower() for e in current}
        entries, report = lists.normalise(name, [*current, *new])
        before = {e.lower() for e in current}
        added = sorted(e for e in entries if e not in before)
        report = {"before": len(current), "new_from_label": len(new), "after": len(entries), "added": added, **report}
        if not self.dry_run and entries != current:
            self.save_list(name, entries)
        log.info("%s list: %s → %s entries, newly listed: %s", name, report["before"], report["after"], added)
        if spec.get("on_new_entry"):
            for entry in added:
                self.run_search(spec["on_new_entry"], {"entry": entry})
        return report

    def save_list(self, name: str, entries: list[str]) -> None:
        """Rewrite one list in lists.yaml. Placeholders elsewhere in the file are kept as written."""
        import yaml
        path = self.engine.config.dir / "lists.yaml"
        raw = yaml.safe_load(path.read_text())
        notes = {(e["value"] if isinstance(e, dict) else e).lower(): e for e in raw[name].get("entries", [])}
        raw[name]["entries"] = [notes.get(v.lower(), v) for v in entries]
        elists.save(raw, path)
        self.engine.reload()

    def forget(self, label: str, ids: list[str]) -> None:
        """Drop messages from a label's sender index (they left the label, e.g. after an unblock)."""
        cache = self.data / "cache" / f"{label.lower()}_index.json"
        if cache.exists():
            index = json.loads(cache.read_text())
            for i in ids:
                index.pop(i, None)
            cache.write_text(json.dumps(index, indent=1, ensure_ascii=False))

    def remove_entries(self, name: str, entries: list[str]) -> list[str]:
        """Take entries off a list (manual jobs such as unblock). Returns the ones that were on it."""
        entries = [e.strip().lower() for e in entries]
        current = self.engine.lists.values(name)
        removed = sorted({e.lower() for e in current} & set(entries))
        self.save_list(name, [e for e in current if e.lower() not in entries])
        return removed
