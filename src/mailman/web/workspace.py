"""What the web interface works on: the configuration files, the test set, and the log.

Every change to a file goes through `check` (does it load, is it consistent, which test emails change
outcome) before `save` writes it. The daemon notices the changed file by itself.
"""

from __future__ import annotations

import asyncio
import json
import shutil
import sqlite3
import tempfile
import os
import time
from pathlib import Path

import re

import yaml

from mailman import gmail
from mailman.engine import validate
from mailman.engine.config import FILES, Config, config_dir
from mailman.engine.core import Engine
from mailman.engine.daemon import data_dir
from mailman.engine.lookups import GmailLookups
from mailman.store import Store
from mailman.web import yamltext


class Workspace:
    def __init__(self):
        self.dir, self.data = config_dir(), data_dir()
        self._engine = Engine(Config(self.dir))
        self.store = Store(self.data / "mailman.db")
        self._svc = None
        self._emails: dict[str, object] = {}
        self._emails_key = ""
        self.overlay: dict[str, dict] = {}   # re-asked answers for a pending jev.yaml edit: id → {question: answer}

    # ------------------------------------------------------------ Gmail (optional)

    def svc(self):
        if self._svc is None:
            try:
                self._svc = gmail.service()
            except Exception:
                return None
        return self._svc

    def lookups(self):
        return GmailLookups(self.svc(), self.data / "mailman.db") if self.svc() else None

    # ------------------------------------------------------------ files

    def text(self, name: str) -> str:
        assert name in FILES
        return (self.dir / f"{name}.yaml").read_text()

    def candidate(self, name: str, text: str) -> Engine:
        """An engine on a copy of the configuration with one file replaced."""
        tmp = Path(tempfile.mkdtemp(prefix="mailman-check-"))
        try:
            for f in FILES:
                shutil.copy(self.dir / f"{f}.yaml", tmp / f"{f}.yaml")
            (tmp / f"{name}.yaml").write_text(text)
            return Engine(Config(tmp))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    @property
    def engine(self) -> Engine:
        """The engine on the configuration as it is on disk now (a job, the daemon, or you may have edited it)."""
        try:
            if self._engine.config.mtime() != self._engine.config.loaded_at:
                self._engine = Engine(Config(self.dir))
        except Exception:
            pass   # an invalid file on disk: keep the last good one; Health reports the problem
        return self._engine

    def reload(self) -> None:
        self._engine = Engine(Config(self.dir))

    # ------------------------------------------------------------ the test set

    def cases(self) -> dict[str, dict]:
        """id → {raw path, jev answers, source, expected?}: the cached sample plus emails you marked."""
        out: dict[str, dict] = {}
        index_path = self.data / "cache/sample_index.json"
        if index_path.exists():
            for mid, meta in json.loads(index_path.read_text()).items():
                probe = self.data / "cache/probe" / f"{mid}.json"
                if probe.exists():
                    out[mid] = {"raw": self.data / "cache/sample" / f"{mid}.json", "probe": probe,
                                "source": meta.get("source") or meta["folder"].lower()}
        for f in sorted((self.data / "testset").glob("*.json")) if (self.data / "testset").exists() else []:
            out[f.stem] = {"case": f, "source": "marked"}
        return out

    def load_case(self, mid: str, case: dict, engine: Engine, pending: bool = True):
        """(email, answers, expected) for one test email. `pending`: with the answers re-asked for an edit
        that is not saved yet (they belong to the candidate, not to the current configuration)."""
        key = json.dumps(engine.config.settings.get("email"), sort_keys=True, default=str)
        if key != self._emails_key:
            self._emails, self._emails_key = {}, key
        if "case" in case:
            data = json.loads(case["case"].read_text())
            raw, answers, expected = data["raw"], data.get("jev", {}), data.get("expected")
        else:
            raw, answers, expected = None, json.loads(case["probe"].read_text())["jev"], None
        if mid not in self._emails:
            self._emails[mid] = engine.parse(raw if raw is not None else json.loads(case["raw"].read_text()))
        return self._emails[mid], {**answers, **(self.overlay.get(mid, {}) if pending else {})}, expected

    def evaluate(self, engine: Engine, pending: bool = True) -> dict[str, dict]:
        lookups = self.lookups()
        out = {}
        for mid, case in self.cases().items():
            email, answers, expected = self.load_case(mid, case, engine, pending)
            facts = engine.facts(email, answers, lookups)
            facts.email = email
            try:
                outcome, rule, labels = engine.decide(facts)
            except Exception as e:   # a rule that cannot be evaluated is a finding, not a crash
                outcome, rule, labels = "error", str(e)[:120], []
            out[mid] = {"outcome": outcome, "rule": rule, "labels": labels, "sender": email.msg.h("from"),
                        "subject": email.msg.h("subject"), "source": case["source"], "expected": expected}
        return out

    def check(self, name: str, text: str) -> dict:
        """Would this file load, is it consistent, and what changes on the test set?"""
        try:
            cand = self.candidate(name, text)
        except Exception as e:
            return {"ok": False, "problems": [f"the file does not load: {str(e)[:400]}"], "changes": [], "total": 0}
        found = validate.problems(cand)
        stale = self.stale_questions(cand) if name in ("jev", "profile", "facts", "settings") else []
        before, after = self.evaluate(self.engine, pending=False), self.evaluate(cand)
        changes = [{"id": mid, "sender": a["sender"], "subject": a["subject"], "source": a["source"],
                    "before": before[mid], "after": a}
                   for mid, a in after.items()
                   if (a["outcome"], a["labels"]) != (before[mid]["outcome"], before[mid]["labels"])]
        broken = [{"id": mid, "sender": a["sender"], "subject": a["subject"], "expected": a["expected"], "after": a}
                  for mid, a in after.items() if a["expected"] and not self.meets(a)]
        return {"ok": not found, "problems": found, "changes": changes, "total": len(after),
                "expectations_broken": broken, "stale_questions": stale}

    @staticmethod
    def meets(result: dict) -> bool:
        e = result["expected"]
        return result["outcome"] == e["decision"] and ("labels" not in e or sorted(result["labels"]) == sorted(e["labels"]))

    # ------------------------------------------------------------ the test set page

    def testset_rows(self, q: str = "", source: str = "", status: str = "", limit: int = 100, offset: int = 0) -> dict:
        """The test set with what the current configuration does with each email and whether that is what you
        expect (`ok`), is not (`broken`), or nothing is expected of it (`unmarked`)."""
        engine, lookups = self.engine, self.lookups()
        q, rows = q.lower(), []
        for mid, case in self.cases().items():
            try:
                email, answers, expected = self.load_case(mid, case, engine, pending=False)
                subject, sender = email.msg.h("subject"), email.msg.h("from")
                if q and q not in sender.lower() and q not in subject.lower() and q not in mid:
                    continue
                if source and source != case["source"]:
                    continue
                facts = engine.facts(email, answers, lookups)
                facts.email = email
                try:
                    outcome, rule, labels = engine.decide(facts)
                except Exception as e:   # a rule that cannot be evaluated is a finding, not a crash
                    outcome, rule, labels = "error", str(e)[:120], []
            except Exception as e:   # a case file that cannot be read is shown, so it can be removed
                email, expected, sender, subject, outcome, rule, labels = None, None, "", f"(unreadable: {str(e)[:80]})", "error", "", []
            res = {"id": mid, "sender": sender, "subject": subject, "source": case["source"], "expected": expected,
                   "outcome": outcome, "rule": rule, "labels": labels}
            res["status"] = "unmarked" if not expected else "ok" if self.meets(res) else "broken"
            rows.append(res)
        counts = {s: sum(r["status"] == s for r in rows) for s in ("ok", "broken", "unmarked")}
        sources = sorted({c["source"] for c in self.cases().values()})
        if status:
            rows = [r for r in rows if r["status"] == status]
        rows.sort(key=lambda r: (r["status"] != "broken", r["source"] != "marked", r["sender"].lower()))
        return {"total": len(rows), "counts": counts, "sources": sources, "rows": rows[offset:offset + limit]}

    def case_detail(self, mid: str) -> dict:
        cases = self.cases()
        if mid not in cases:
            raise KeyError(mid)
        engine = self.engine
        email, answers, expected = self.load_case(mid, cases[mid], engine, pending=False)
        facts = engine.facts(email, answers, self.lookups())
        facts.email = email
        outcome, rule, labels = engine.decide(facts)
        note = json.loads(cases[mid]["case"].read_text()).get("note", "") if "case" in cases[mid] else ""
        return {"id": mid, "source": cases[mid]["source"], "marked": "case" in cases[mid], "expected": expected,
                "note": note, "answers": answers, "outcome": outcome, "rule": rule, "labels": labels,
                "headers": {h: email.msg.h(h) for h in ("from", "to", "cc", "reply-to", "subject", "date") if email.msg.h(h)},
                "snippet": email.msg.snippet, "body": email.body}

    def save_case(self, mid: str, expected: dict | None, note: str, answers: dict | None) -> None:
        """Set what you expect of a test email (or clear it), with a note and the classifier's answers. An email
        from the cached sample joins the marked ones the first time it is changed."""
        cases = self.cases()
        if mid not in cases or not re.fullmatch(r"[\w.\-]+", mid):
            raise KeyError(mid)
        case = cases[mid]
        if "case" in case:
            data = json.loads(case["case"].read_text())
        else:
            data = {"raw": json.loads(case["raw"].read_text()), "jev": json.loads(case["probe"].read_text())["jev"]}
        data["expected"], data["note"] = expected, note
        if answers is not None:
            data["jev"] = answers
        target = self.data / "testset" / f"{mid}.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False))
        tmp.replace(target)
        self._emails.pop(mid, None)

    def remove_case(self, mid: str) -> None:
        """Take a marked email out of the test set (an email of the cached sample comes back as unmarked)."""
        case = self.cases().get(mid)
        if case is None or "case" not in case:
            raise KeyError(mid)
        case["case"].unlink()
        self._emails.pop(mid, None)

    def stale_questions(self, cand: Engine) -> list[str]:
        """Questions whose text as sent to Jev differs from the current configuration (stored answers are stale)."""
        cases = self.cases()
        if not cases:
            return []
        mid = next(iter(cases))
        email, answers, _ = self.load_case(mid, cases[mid], cand)
        old_f, new_f = self.engine.facts(email, answers), cand.facts(email, answers)
        out = []
        for n in cand.jev.questions:
            if n not in self.engine.jev.questions or \
                    cand.jev.build(n, new_f).model_dump() != self.engine.jev.build(n, old_f).model_dump() or \
                    cand.jev.state(new_f) != self.engine.jev.state(old_f):
                out.append(n)
        return out

    async def reask(self, name: str, text: str, questions: list[str]) -> dict:
        """Ask the test set just these questions under the candidate configuration (kept until saved)."""
        cand = self.candidate(name, text)
        cases = self.cases()
        sem = asyncio.Semaphore(cand.config.settings["jev"].get("concurrency", 8))
        tokens = 0

        async def one(client, mid: str):
            email, answers, _ = self.load_case(mid, cases[mid], cand)
            facts = cand.facts(email, answers)
            got, t = await cand.jev.ask(client, facts, sem, only=questions)
            self.overlay.setdefault(mid, {}).update(got)
            return t

        async with cand.jev.client() as client:
            results = await asyncio.gather(*(one(client, m) for m in cases), return_exceptions=True)
        tokens = sum(r for r in results if isinstance(r, int))
        return {"asked": sum(isinstance(r, int) for r in results), "failed": sum(isinstance(r, Exception) for r in results),
                "tokens": tokens}

    def save(self, name: str, text: str) -> dict:
        result = self.check(name, text)
        if not result["ok"]:
            return result
        path = self.dir / f"{name}.yaml"
        tmp = path.with_suffix(".tmp")
        tmp.write_text(text)
        tmp.replace(path)
        if self.overlay:   # re-asked answers become the stored ones
            for mid, answers in self.overlay.items():
                case = self.cases().get(mid, {})
                target = case.get("probe") or case.get("case")
                if target:
                    data = json.loads(target.read_text())
                    data["jev"] = {**data.get("jev", {}), **answers}
                    target.write_text(json.dumps(data, ensure_ascii=False, indent=1))
            self.overlay = {}
        self.reload()
        self.store.event("config_saved", f"{name}.yaml · {len(result['changes'])} of {result['total']} test emails change")
        return result

    # ------------------------------------------------------------ structured edits (return candidate text)

    def set_node(self, name: str, path: list, snippet: str | None) -> str:
        """Replace, add, or (snippet None) delete the entry at `path`. Every other line stays as it is."""
        return yamltext.set_entry(self.text(name), [str(k) for k in path], snippet)

    @staticmethod
    def rule_block(r: dict) -> list[str]:
        """A rule as the lines of a sequence item: conditions in compact flow style."""
        out = []
        for k in ("name", "note", "enabled", "leftover", "when", "unless", "then", "label"):
            if k not in r or r[k] in (None, "", {}) or (k == "enabled" and r[k] is True) or (k == "leftover" and not r[k]):
                continue
            v = yaml.safe_dump(r[k], default_flow_style=True, allow_unicode=True, width=10000, sort_keys=False).strip()
            out.append(f"{'  - ' if not out else '    '}{k}: {v.removesuffix('...').strip()}")
        return out

    def edit_rule(self, stage: str, op: str, index: int | None, rule: dict | None = None, to: int | None = None) -> str:
        text = self.text("rules")
        if not re.search(rf"^{re.escape(stage)}:", text, re.M):
            text = text.rstrip("\n") + f"\n\n{stage}:\n"
        lines, lo, hi, blocks = yamltext.items(text, stage)
        if op == "set":
            if index is None or index >= len(blocks):
                blocks.append(([""] if blocks else []) + self.rule_block(rule))
            else:   # keep the blank lines and comments in front of, and behind, the old rule
                old = blocks[index]
                first = next(i for i, l in enumerate(old) if l.lstrip().startswith("- "))
                tail = []
                while old and (not old[-1].strip() or old[-1].lstrip().startswith("#")) and len(old) - 1 > first:
                    tail.insert(0, old.pop())
                blocks[index] = old[:first] + self.rule_block(rule) + tail
        elif op == "delete":
            del blocks[index]
        elif op == "move":
            item = blocks.pop(index)
            blocks.insert(max(0, min(to, len(blocks))), item)
        elif op == "toggle":
            b = blocks[index]
            off = next((i for i, l in enumerate(b) if l.strip() == "enabled: false"), None)
            if off is not None:
                del b[off]
            else:
                first = next(i for i, l in enumerate(b) if l.lstrip().startswith("- "))
                b.insert(first + 1, "    enabled: false")
        if blocks and not any(not l.strip() for l in blocks[-1][-1:]) and hi < len(lines) and lines[hi].strip():
            blocks[-1].append("")
        return yamltext.put_items(lines, lo, hi, blocks)

    def edit_list(self, name: str, body: dict | None) -> str:
        """Replace a list (header and entries), or delete it with body None. lists.yaml is machine-written."""
        from mailman.engine import lists as elists
        raw = yaml.safe_load(self.text("lists"))
        if body is None:
            raw.pop(name, None)
        else:
            raw[name] = body
        tmp = Path(tempfile.mkdtemp(prefix="mailman-list-")) / "lists.yaml"
        elists.save(raw, tmp)
        text = tmp.read_text()
        shutil.rmtree(tmp.parent, ignore_errors=True)
        return text

    # ------------------------------------------------------------ the log

    def log_rows(self, q: str = "", decision: str = "", rule: str = "", days: int = 30, limit: int = 200,
                 offset: int = 0) -> dict:
        db = sqlite3.connect(self.data / "mailman.db")
        where, args = ["ts >= datetime('now', ?)"], [f"-{days} days"]
        if q:
            where.append("(sender LIKE ? OR subject LIKE ?)")
            args += [f"%{q}%", f"%{q}%"]
        if decision:
            where.append("decision = ?")
            args.append(decision)
        if rule:
            where.append("rule LIKE ?")
            args.append(f"%{rule}%")
        sql = " FROM actions WHERE " + " AND ".join(where)
        total = db.execute("SELECT COUNT(*)" + sql, args).fetchone()[0]
        rows = db.execute("SELECT id, ts, gmail_id, sender, subject, decision, rule, action, dry_run, error" + sql +
                          " ORDER BY id DESC LIMIT ? OFFSET ?", [*args, limit, offset]).fetchall()
        keys = ("row", "ts", "gmail_id", "sender", "subject", "decision", "rule", "action", "dry_run", "error")
        return {"total": total, "rows": [dict(zip(keys, r)) for r in rows]}

    def log_row(self, row: int) -> dict:
        db = sqlite3.connect(self.data / "mailman.db")
        r = db.execute("SELECT id, ts, gmail_id, thread_id, sender, subject, decision, rule, action, dry_run, error, "
                       "tokens, facts, jev FROM actions WHERE id = ?", (row,)).fetchone()
        keys = ("row", "ts", "gmail_id", "thread_id", "sender", "subject", "decision", "rule", "action", "dry_run",
                "error", "tokens", "facts", "jev")
        d = dict(zip(keys, r))
        d["facts"], d["jev"] = json.loads(d["facts"] or "{}"), json.loads(d["jev"] or "{}")
        d["rules"] = self.rule_definitions(d["rule"])
        return d

    def rule_definitions(self, explanation: str) -> list[dict]:
        """The current definitions of the rules named in a log row's explanation."""
        import re
        names = [n.strip() for part in re.split(r"[;,]", explanation.split(" (rescued")[0]) for n in [part] if n.strip()]
        names = [n.split(": ")[-1] for n in names]
        out = []
        for st in self.engine.config.rules["stages"]:
            for r in self.engine.config.rules.get(st["name"], []):
                if r["name"] in names:
                    out.append({"stage": st["name"], **r})
        return out

    def stats(self, days: int = 30) -> dict[str, int]:
        """How many emails each rule decided in the last days."""
        db = sqlite3.connect(self.data / "mailman.db")
        counts: dict[str, int] = {}
        for rule, n in db.execute("SELECT rule, COUNT(*) FROM actions WHERE ts >= datetime('now', ?) AND rule != '' "
                                  "GROUP BY rule", (f"-{days} days",)):
            import re
            for name in re.split(r"[;,]", rule.split(" (rescued")[0].split(": ")[-1]):
                if name.strip():
                    counts[name.strip()] = counts.get(name.strip(), 0) + n
        return counts

    @staticmethod
    def alerts(age: int | None, state: dict, waiting: int, config_problems: list[str]) -> list[dict]:
        """What needs your attention, most urgent first: {title, detail, fix}."""
        def since(value: str) -> tuple[str, str]:
            ts, _, text = value.partition(" ")
            return time.strftime("%d %b %H:%M", time.localtime(int(ts))) if ts.isdigit() else "", text or value

        out = []
        token = os.environ.get("MAILMAN_TOKEN_FILE", "")
        signed_in = bool(os.environ.get("GMAIL_REFRESH_TOKEN")) or bool(token and os.path.exists(token))
        if signed_in and (age is None or age >= 900):   # without a sign-in the daemon waits; that alert follows
            out.append({"title": "The daemon is not running", "fix": "Start it again. Until then no mail is handled.",
                        "detail": "it has never run here" if age is None else f"last sign of life {age // 60} minutes ago"})
        if state.get("profile_error"):
            out.append({"title": "The profile is not yours yet, so no mail is handled",
                        "detail": state["profile_error"],
                        "fix": "Import your installation on the Backup page, or put your own names and addresses "
                               "in Profile (your mailbox's address must be among them). Mail is handled as soon as it is."})
        if state.get("classifier_error"):
            when, text = since(state["classifier_error"])
            low = text.lower()
            fix = ("Add credits (and turn on auto-reload) at console.typesafe.ai." if "402" in text or "credit" in low else
                   "Check the TypeSafe API key." if "401" in text or "403" in text or "api key" in low else
                   "It is retried every half hour; if it lasts, check the TypeSafe service and the key.")
            out.append({"title": "The classifier is not answering", "detail": f"since {when}: {text}",
                        "fix": fix + " Waiting mail is retried on its own once it works again."})
        if not signed_in:
            out.append({"title": "Gmail is not connected", "detail": "no sign-in has been stored yet",
                        "fix": "Open Health and press Connect Gmail. The daemon starts by itself once you have."})
        elif state.get("gmail_error"):
            when, text = since(state["gmail_error"])
            low = text.lower()
            fix = ("Connect Gmail again on the Health page: the sign-in has expired or was withdrawn."
                   if "invalid_grant" in low or "expired" in low or "revoked" in low or "authenticat" in low else
                   "It reconnects on its own; if it lasts, check the network and the Gmail sign-in.")
            out.append({"title": "Gmail is not reachable", "detail": f"since {when}: {text}", "fix": fix})
        if state.get("config_error"):
            out.append({"title": "The daemon refused the current configuration", "detail": state["config_error"],
                        "fix": "It keeps working on the last good one. Fix the file, or restart the daemon if it "
                               "asks for a newer engine."})
        if config_problems:
            out.append({"title": "The configuration has problems", "detail": "; ".join(config_problems[:3]),
                        "fix": "Open the page of the rule, flag or list it names and correct it."})
        if waiting:
            out.append({"title": f"{waiting} email{'s' if waiting != 1 else ''} could not be handled and "
                                 f"{'are' if waiting != 1 else 'is'} waiting in the inbox",
                        "detail": "every attempt in the last 7 days failed", "fix": "See Logs for the errors."})
        if not os.environ.get("TYPESAFE_API_KEY"):
            out.append({"title": "No classifier key is set", "detail": "TYPESAFE_API_KEY is empty",
                        "fix": "Set the key in the app's options (or the environment) and restart."})
        return out

    def health(self) -> dict:
        db = sqlite3.connect(self.data / "mailman.db")
        beat = db.execute("SELECT value FROM state WHERE key = 'heartbeat'").fetchone()
        last = db.execute("SELECT ts, sender, action FROM actions ORDER BY id DESC LIMIT 1").fetchone()
        errors = db.execute("SELECT COUNT(*) FROM actions WHERE error IS NOT NULL AND ts >= datetime('now', '-1 day')"
                            ).fetchone()[0]
        try:
            found = validate.problems(Engine(Config(self.dir)))
        except Exception as e:
            found = [str(e)[:300]]
        age = int(time.time()) - int(beat[0]) if beat else None
        state = dict(db.execute("SELECT key, value FROM state WHERE key IN ('classifier_error', 'gmail_error', 'config_error', 'profile_error')"))
        waiting = db.execute("SELECT COUNT(*) FROM (SELECT gmail_id FROM actions WHERE ts >= datetime('now', '-7 days') "
                             "GROUP BY gmail_id HAVING SUM(error IS NULL) = 0)").fetchone()[0]
        alerts = self.alerts(age, state, waiting, found)
        return {"alerts": alerts, "waiting": waiting,
                "daemon_seconds_since_heartbeat": age, "daemon_running": age is not None and age < 900,
                "last_email": {"ts": last[0], "sender": last[1], "action": last[2]} if last else None,
                "errors_last_day": errors, "config_problems": found, "test_emails": len(self.cases()),
                "events": [dict(zip(("ts", "kind", "detail"), r)) for r in
                           db.execute("SELECT ts, kind, detail FROM events ORDER BY id DESC LIMIT 15")]}
