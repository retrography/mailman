"""mailman CLI."""

from __future__ import annotations

import argparse
import os
from pathlib import Path

from mailman import gmail

ROOT = Path(__file__).resolve().parents[2]


def cmd_auth(args: argparse.Namespace) -> None:
    """One-time local OAuth consent. Writes an authorized-user token (gitignored).

    For the routine, copy client_id / client_secret / refresh_token from that file into the
    GMAIL_CLIENT_ID / GMAIL_CLIENT_SECRET / GMAIL_REFRESH_TOKEN secrets.
    """
    from google_auth_oauthlib.flow import InstalledAppFlow

    flow = InstalledAppFlow.from_client_secrets_file(args.client_secrets, gmail.SCOPES)
    creds = flow.run_local_server(port=0, prompt="consent", access_type="offline")
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(creds.to_json())
    os.chmod(out, 0o600)
    print(f"token written to {out}")


def cmd_daemon(args: argparse.Namespace) -> None:
    from mailman.engine import daemon

    daemon.main(dry_run=True if args.dry_run else None, poll=args.poll)


def runner(dry_run: bool = False):
    """A job runner outside the daemon (manual jobs)."""
    import logging

    from mailman.engine import daemon
    from mailman.engine.core import Engine
    from mailman.engine.jobs import Runner
    from mailman.store import Store

    daemon.load_env()
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    for noisy in ("httpx", "httpx2", "typesafe_sdk", "googleapiclient.discovery_cache", "googleapiclient.http"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    return Runner(Engine(), gmail.service(), Store(daemon.data_dir() / "mailman.db"), daemon.data_dir(), dry_run)


def cmd_check(args: argparse.Namespace) -> None:
    """Load the configuration and report what it holds (fails on an invalid file)."""
    from mailman.engine.core import Engine

    e = Engine()
    c = e.config
    print(f"configuration: {c.dir}")
    print(f"  lists      {', '.join(f'{n} ({len(c.lists[n].get('entries', []))})' for n in e.lists.names())}")
    print(f"  facts      {len(c.facts)}")
    print(f"  questions  {len(e.jev.questions)} ({len(e.jev.first())} in the first request)")
    print(f"  rules      {', '.join(f'{st['name']} ({len(c.rules.get(st['name'], []))})' for st in c.rules['stages'])}")
    print(f"  outcomes   {', '.join(c.outcomes)}")
    print(f"  jobs       {', '.join(c.jobs['jobs'])}")
    from mailman.engine.validate import problems

    found = problems(e)
    print("  consistent: no problems found" if not found else "  PROBLEMS:\n    " + "\n    ".join(found))
    if found:
        raise SystemExit(1)


def cmd_sync(args: argparse.Namespace) -> None:
    """Fill a list from its Gmail label, normalise it, and run its on_new_entry job."""
    import json

    print(json.dumps(runner(args.dry_run).sync_list(args.list), indent=1))


def cmd_unblock(args: argparse.Namespace) -> None:
    """Take entries off the blocked list; their mail leaves Blocked and goes back to the inbox."""
    r = runner()
    print(f"removed from list: {r.remove_entries('blocked', args.entries)}")
    ids = [i for e in args.entries for i in r.run_search("unblock", {"entry": e.strip().lower()})]
    r.forget("Blocked", ids)
    print(f"{len(set(ids))} messages moved from Blocked back to the inbox")


def cmd_clean_label(args: argparse.Namespace) -> None:
    """Apply the current rules to the mail already in a label: first a preview, with --apply for real."""
    import asyncio
    from collections import Counter

    r = runner()
    params = {"label": args.label, "relabel": args.relabel, "limit": args.limit}
    done = asyncio.run(r.classify_search("clean_label", params))
    todo = {i: x for i, x in done.items() if "error" not in x and not x.get("applied")}
    print(f"{len(done)} messages in {args.label}: {sum('error' in x for x in done.values())} errors, "
          f"{len(done) - len(todo)} already applied or failed")
    for (decision, labels), n in Counter((x["decision"], ",".join(x["labels"])) for x in todo.values()).most_common():
        print(f"  {n:5}  {decision:7} {labels}")
    if args.apply:
        print("applied:", r.apply_classified("clean_label", params, done))
    else:
        print("nothing changed — run again with --apply to do this")


def cmd_export(args: argparse.Namespace) -> None:
    """This installation as one file to import elsewhere: the configuration, and unless --config-only also the
    log (history, undo, what is known about senders) and the test set (stored emails with the classifier's
    answers, which test-before-save runs on). Never the Gmail sign-in."""
    import sqlite3
    import tempfile
    import zipfile

    from mailman.engine import daemon
    from mailman.engine.config import config_dir

    data, out = daemon.data_dir(), Path(args.out)
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for f in sorted(config_dir().glob("*.yaml")):
            z.write(f, f"config/{f.name}")
        if args.config_only:
            print(f"{out} ({out.stat().st_size / 1e3:.0f} kB): the configuration only. It holds personal details: keep it private.")
            return
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
    print(f"{out} ({out.stat().st_size / 1e6:.0f} MB): configuration, log and test set. It contains personal mail: "
          f"keep it private. The Gmail sign-in is not in it.")


def cmd_web(args: argparse.Namespace) -> None:
    """The web interface (configuration, log, test-before-save)."""
    import uvicorn

    from mailman.engine import daemon
    from mailman.web.app import create

    daemon.load_env()
    uvicorn.run(create(), host=args.host, port=args.port, log_level="warning")


def main() -> None:
    p = argparse.ArgumentParser(prog="mailman")
    sub = p.add_subparsers(required=True)
    a = sub.add_parser("auth", help="one-time Gmail OAuth consent (local)")
    a.add_argument("--client-secrets", default=str(ROOT / "attic/py/data/credentials.json"))
    a.add_argument("--out", default=str(ROOT / "data/cache/token.json"))
    a.set_defaults(fn=cmd_auth)
    d = sub.add_parser("daemon", help="watch the mailbox and run the triggers of config/jobs.yaml")
    d.add_argument("--dry-run", action="store_true", help="log decisions without changing Gmail")
    d.add_argument("--poll", type=int, default=None, help="poll at least every N seconds (default: settings)")
    d.set_defaults(fn=cmd_daemon)
    c = sub.add_parser("check", help="load and summarise the configuration")
    c.set_defaults(fn=cmd_check)
    s = sub.add_parser("sync", help="fill a list from its Gmail label (e.g. `mailman sync blocked`)")
    s.add_argument("list")
    s.add_argument("--dry-run", action="store_true")
    s.set_defaults(fn=cmd_sync)
    u = sub.add_parser("unblock", help="take entries off the blocked list and move their mail back")
    u.add_argument("entries", nargs="+")
    u.set_defaults(fn=cmd_unblock)
    k = sub.add_parser("clean-label", help="apply the current rules to the mail already in a label")
    k.add_argument("label")
    k.add_argument("--apply", action="store_true", help="perform the outcomes (default: preview only)")
    k.add_argument("--relabel", action="store_true", help="take the label off mail the rules no longer give it to")
    k.add_argument("--limit", type=int, default=0, help="only the newest N messages of the label")
    k.set_defaults(fn=cmd_clean_label)
    x = sub.add_parser("export", help="configuration, log and test set as one file, to import in another installation")
    x.add_argument("out", nargs="?", default="mailman-export.zip")
    x.add_argument("--config-only", action="store_true",
                   help="only the configuration files: no log, no undo history, no test set")
    x.set_defaults(fn=cmd_export)
    w = sub.add_parser("web", help="the web interface (default http://127.0.0.1:8377)")
    w.add_argument("--host", default="127.0.0.1")
    w.add_argument("--port", type=int, default=8377)
    w.set_defaults(fn=cmd_web)
    args = p.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
