"""The web interface: a small API over the workspace, and the static single-page app.

Run with `mailman web` (default http://127.0.0.1:8377). No login of its own: bind it to localhost, or put it
behind something that authenticates (Home Assistant Ingress).
"""

from __future__ import annotations

import json
import shutil
import threading
import asyncio
import os
from collections import Counter
from email.utils import parseaddr
from pathlib import Path

import yaml
from fastapi import Body, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from mailman import gmail
from mailman.engine import outcomes as eo
from mailman.engine import validate
from mailman.engine.config import FILES
from mailman.engine.jobs import Runner
from mailman.web.workspace import Workspace
from mailman.web import hass
from mailman.store import Store

STATIC = Path(__file__).parent / "static"


class _NoAliases(yaml.SafeDumper):
    def ignore_aliases(self, data):   # write repeated values out in full: an entry must stand on its own
        return True


def plain_dump(data) -> str:
    text = yaml.dump(data, Dumper=_NoAliases, sort_keys=False, allow_unicode=True, width=100)
    return text.removesuffix("...\n")   # the end marker PyYAML adds after a bare value


try:
    from importlib.metadata import version as _version
    VERSION = _version("mailman")
except Exception:   # run from a source tree that was never installed
    VERSION = "dev"

PASTE_REDIRECT = "http://localhost:8765/"   # nothing needs to listen there: the address itself carries the code


def create() -> FastAPI:
    app = FastAPI(title="mailman", docs_url=None, redoc_url=None)
    allow = {a.strip() for a in os.environ.get("MAILMAN_ALLOW_FROM", "").split(",") if a.strip()}
    if allow:   # as a Home Assistant app only its Ingress proxy may connect; it has done the login
        @app.middleware("http")
        async def only_allowed(request: Request, call_next):
            if request.client is None or request.client.host not in allow:
                return HTMLResponse("mailman is reached through Home Assistant", status_code=403)
            return await call_next(request)
    ws = Workspace()
    app.state.ws = ws
    hass.watch(ws.health)   # as a Home Assistant app: the alerts appear there too

    def need_gmail():
        svc = ws.svc()
        if svc is None:
            raise HTTPException(503, "Gmail is not connected")
        return svc

    def file_name(name: str) -> str:
        if name not in FILES:
            raise HTTPException(404, f"no such configuration file: {name}")
        return name

    # ------------------------------------------------------------ configuration

    @app.get("/api/health")
    def health():
        return {**ws.health(), "version": VERSION}

    @app.get("/api/config/{name}")
    def config(name: str):
        return {"name": file_name(name), "text": ws.text(name), "data": getattr(ws.engine.config, name)}

    @app.post("/api/check/{name}")
    def check(name: str, text: str = Body(..., embed=True)):
        return ws.check(file_name(name), text)

    @app.put("/api/config/{name}")
    def save(name: str, text: str = Body(..., embed=True)):
        return ws.save(file_name(name), text)

    @app.post("/api/reask/{name}")
    async def reask(name: str, text: str = Body(...), questions: list[str] = Body(...)):
        return await ws.reask(file_name(name), text, questions)

    @app.post("/api/node/{name}")
    def node(name: str, path: list = Body(...), snippet: str | None = Body(None)):
        try:
            return {"text": ws.set_node(file_name(name), path, snippet)}
        except Exception as e:
            raise HTTPException(400, f"cannot apply the edit: {e}")

    @app.post("/api/yaml/dump")
    def yaml_dump(data=Body(None, embed=True)):
        return {"text": plain_dump(data)}

    @app.post("/api/yaml/load")
    def yaml_load(text: str = Body(..., embed=True)):
        try:
            return {"data": yaml.safe_load(text)}
        except yaml.YAMLError as e:
            raise HTTPException(400, f"not valid YAML: {e}")

    @app.get("/api/catalogue")
    def catalogue():
        e = ws.engine
        labels = []
        if ws.svc():
            try:
                labels = sorted(l["name"] for l in gmail._execute(ws.svc().users().labels().list(userId="me"))["labels"]
                                if l["type"] == "user")
            except Exception:
                labels = []
        return {"facts": validate.catalogue(e),
                "lists": [{"name": n, "holds": e.lists.kind(n), "count": len(e.config.lists[n].get("entries", [])),
                           "description": e.config.lists[n].get("description", "")} for n in e.lists.names()],
                "outcomes": {n: o.get("help", "") for n, o in e.config.outcomes.items()},
                "stages": e.config.rules["stages"], "labels": labels, "stats": ws.stats()}

    # ------------------------------------------------------------ rules and lists (structured edits → text)

    @app.post("/api/rules/edit")
    def rules_edit(stage: str = Body(...), op: str = Body(...), index: int | None = Body(None),
                   rule: dict | None = Body(None), to: int | None = Body(None)):
        return {"text": ws.edit_rule(stage, op, index, rule, to)}

    @app.post("/api/lists/edit")
    def lists_edit(name: str = Body(...), body: dict | None = Body(None)):
        return {"text": ws.edit_list(name, body)}

    @app.get("/api/lists/{name}")
    def list_detail(name: str):
        raw = yaml.safe_load(ws.text("lists"))
        if name not in raw:
            raise HTTPException(404, "no such list")
        used = [f"{st['name']} / {r['name']}" for st in ws.engine.config.rules["stages"]
                for r in ws.engine.config.rules.get(st["name"], [])
                if any(k == "list" and n == name for c in (r.get("when", {}), r.get("unless", {}))
                       for k, n in validate.condition_names(c))]
        used += [f"fact {f}" for f, spec in ws.engine.config.facts.items()
                 if name in json.dumps(spec.get("list", {}).get("on", "")) or
                 any(k == "list" and n == name for case in spec.get("derived", []) if "when" in case
                     for k, n in validate.condition_names(case["when"]))]
        return {"name": name, "body": raw[name], "used_by": used}

    @app.get("/api/candidates")
    def candidates(q: str, limit: int = 300):
        """Senders of the mail a Gmail search finds, with counts — to pick list entries from."""
        svc = need_gmail()
        res = gmail._execute(svc.users().messages().list(userId="me", q=q, maxResults=min(limit, 500)))
        counts: Counter = Counter()
        example: dict[str, str] = {}
        for m in res.get("messages", []):
            meta = gmail._execute(svc.users().messages().get(userId="me", id=m["id"], format="metadata",
                                                             metadataHeaders=["From", "Subject"]))
            h = {x["name"]: x["value"] for x in meta["payload"].get("headers", [])}
            a = parseaddr(h.get("From", ""))[1].lower()
            counts[a] += 1
            example.setdefault(a, h.get("Subject", ""))
        return [{"address": a, "count": n, "example": example[a]} for a, n in counts.most_common()]

    # ------------------------------------------------------------ the log

    @app.get("/api/log")
    def log(q: str = "", decision: str = "", rule: str = "", days: int = 30, limit: int = 200, offset: int = 0):
        return ws.log_rows(q, decision, rule, days, limit, offset)

    @app.get("/api/log/{row}")
    def log_row(row: int):
        return ws.log_row(row)

    @app.post("/api/log/{row}/undo")
    def undo(row: int):
        svc = need_gmail()
        d = ws.log_row(row)
        spec = ws.engine.config.outcomes.get(d["decision"])
        if not spec or "undo" not in spec:
            raise HTTPException(400, f"the outcome “{d['decision']}” has no undo")
        labels = [l for l in d["action"].split(" (")[0].split(" ", 1)[1].split(",")] if " " in d["action"] else []
        eo.apply(svc, eo.Labels(svc), {"do": spec["undo"]}, labels, [d["gmail_id"]])
        ws.store.log(gmail_id=d["gmail_id"], thread_id=d["thread_id"] or "", sender=d["sender"] or "",
                     subject=d["subject"] or "", decision="undo", rule=f"undo of #{row} ({d['rule']})",
                     action=f"undo {d['action']}", dry_run=False)
        return {"ok": True}

    @app.post("/api/log/{row}/feedback")
    def feedback(row: int, decision: str = Body(...), labels: list[str] = Body([]), note: str = Body("")):
        """“This was wrong”: the email joins the test set with the outcome you expected."""
        svc = need_gmail()
        d = ws.log_row(row)
        (ws.data / "testset").mkdir(parents=True, exist_ok=True)
        case = {"raw": gmail.get(svc, d["gmail_id"]), "jev": d["jev"],
                "expected": {"decision": decision, "labels": labels}, "note": note, "from_log_row": row}
        (ws.data / "testset" / f"{d['gmail_id']}.json").write_text(json.dumps(case, ensure_ascii=False))
        ws.store.event("feedback", f"{d['sender']} · expected {decision} {','.join(labels)}")
        return {"ok": True}

    # ------------------------------------------------------------ trying things on one email

    @app.get("/api/testset")
    def testset(q: str = "", limit: int = 60):
        q = q.lower()
        out = []
        for mid, case in ws.cases().items():
            email, _, expected = ws.load_case(mid, case, ws.engine)
            s, subj = email.msg.h("from"), email.msg.h("subject")
            if not q or q in s.lower() or q in subj.lower() or q in mid:
                out.append({"id": mid, "sender": s, "subject": subj, "source": case["source"], "expected": expected})
            if len(out) >= limit:
                break
        return out

    @app.post("/api/try")
    def try_facts(id: str = Body(...), name: str | None = Body(None), text: str | None = Body(None)):
        """Every fact's value for one test email — under the current configuration, or a candidate file."""
        engine = ws.candidate(name, text) if name and text is not None else ws.engine
        cases = ws.cases()
        if id not in cases:
            raise HTTPException(404, "not in the test set")
        email, answers, _ = ws.load_case(id, cases[id], engine)
        facts = engine.facts(email, answers, ws.lookups())
        values = {}
        for f in engine.config.facts:
            try:
                values[f] = facts.get(f)
            except Exception as e:
                values[f] = f"error: {e}"
        outcome, rule, labels = engine.decide(facts)
        return {"facts": values, "answers": answers, "outcome": outcome, "rule": rule, "labels": labels,
                "sender": email.msg.h("from"), "subject": email.msg.h("subject")}

    @app.post("/api/jev/preview")
    def jev_preview(id: str = Body(...), name: str | None = Body(None), text: str | None = Body(None)):
        """What Jev is shown for one test email, and every question as it would be sent."""
        engine = ws.candidate(name, text) if name and text is not None else ws.engine
        cases = ws.cases()
        email, answers, _ = ws.load_case(id, cases[id], engine)
        facts = engine.facts(email, answers)
        return {"input": engine.jev.state(facts),
                "questions": {n: engine.jev.build(n, facts).model_dump() for n in engine.jev.questions},
                "first": engine.jev.first()}

    # ------------------------------------------------------------ jobs

    @app.post("/api/jobs/{name}/run")
    def run_job(name: str, params: dict = Body({}), dry_run: bool = Body(True)):
        svc = need_gmail()
        job = ws.engine.config.jobs["jobs"].get(name)
        if not job:
            raise HTTPException(404, "no such job")
        runner = Runner(ws.engine, svc, ws.store, ws.data, dry_run)
        if "sync_list" in job:
            result = runner.sync_list(job["sync_list"])
        elif "search" in job and "outcome" in job:
            job_enabled = job.get("enabled", True)
            ids = gmail.search(svc, job["search"].format(**params)) if dry_run or not job_enabled else \
                runner.run_search(name, params)
            result = {"messages": len(ids), "search": job["search"].format(**params), "outcome": job["outcome"],
                      "note": "" if job_enabled else "this job is disabled in jobs.yaml"}
        else:
            raise HTTPException(400, "this job runs from the command line (mailman clean-label) or a trigger")
        if not dry_run:
            ws.store.event("job_run", f"{name} {json.dumps(params)}")
            ws.reload()
        return {"dry_run": dry_run, "result": result}

    # ------------------------------------------------------------ clean-up of a label

    cleanups: dict[str, dict] = {}   # label → {running, error, total}: the classification runs in a thread
    REMOVES = ("delete", "spam", "block", "block_everywhere")

    def cleanup_file(label: str) -> Path:
        return ws.data / "cache" / f"clean_label_{label}.json"

    def cleanup_records(label: str) -> dict:
        path = cleanup_file(label)
        return json.loads(path.read_text()) if path.exists() else {}

    def cleanup_idle(label: str) -> None:
        if cleanups.get(label, {}).get("running"):
            raise HTTPException(409, "the preview of this label is still running")

    @app.get("/api/cleanup/{label}")
    def cleanup_status(label: str):
        """Where the clean-up of a label stands: progress, what would happen, and every removal for review."""
        done, state = cleanup_records(label), cleanups.get(label, {})
        open_ = {i: r for i, r in done.items() if "error" not in r and not r.get("applied")}
        groups: dict[str, int] = {}
        for r in open_.values():
            key = f"{r['decision']} → {', '.join(r['labels'])}" if r["labels"] else r["decision"]
            groups[key] = groups.get(key, 0) + 1
        errors = [r["error"] for r in done.values() if "error" in r]
        return {"running": bool(state.get("running")), "error": state.get("error"), "total": state.get("total"),
                "classified": len(done), "applied": sum(1 for r in done.values() if r.get("applied")),
                "failed": len(errors), "failure": errors[0] if errors else None,
                "outcomes": sorted(groups.items(), key=lambda kv: -kv[1]),
                "removals": [{"id": i, "sender": r["sender"], "subject": r["subject"], "decision": r["decision"],
                              "rule": r["rule"].split(";")[0]} for i, r in open_.items() if r["decision"] in REMOVES]}

    @app.post("/api/cleanup/{label}/start")
    def cleanup_start(label: str, limit: int | None = Body(None), fresh: bool = Body(False)):
        """Classify the mail in a label with the current rules, in the background. Nothing in Gmail changes."""
        need_gmail()
        cleanup_idle(label)
        if fresh and cleanup_file(label).exists():
            cleanup_file(label).unlink()
        state = cleanups[label] = {"running": True, "error": None, "total": None}

        def work() -> None:
            try:
                svc = gmail.service()   # its own connection and database handle: this is another thread
                state["total"] = len(gmail.search(svc, f"label:{label}")[:limit or None])
                runner = Runner(ws.engine, svc, Store(ws.data / "mailman.db"), ws.data, dry_run=False)
                asyncio.run(runner.classify_search("clean_label", {"label": label, "limit": limit}))
            except Exception as e:
                state["error"] = str(e)[:300]
            finally:
                state["running"] = False

        threading.Thread(target=work, name=f"cleanup-{label}", daemon=True).start()
        return {"started": True}

    @app.post("/api/cleanup/{label}/spare")
    def cleanup_spare(label: str, ids: list[str] = Body(..., embed=True)):
        """Take emails out of the removals: they stay where they are, under this label."""
        cleanup_idle(label)
        done = cleanup_records(label)
        for i in ids:
            r = done.get(i)
            if r and "error" not in r and not r.get("applied") and r["decision"] in REMOVES:
                r["rule"] = f"spared by hand, was: {r['decision']} · {r['rule']}"
                r["decision"], r["labels"] = "keep", [label]
        cleanup_file(label).write_text(json.dumps(done, ensure_ascii=False, default=str))
        return cleanup_status(label)

    @app.post("/api/cleanup/{label}/apply")
    def cleanup_apply(label: str, relabel: bool = Body(True, embed=True)):
        """Do it: move, relabel and trash as previewed. Every change is logged and can be undone from Logs."""
        svc = need_gmail()
        cleanup_idle(label)
        runner = Runner(ws.engine, svc, ws.store, ws.data, dry_run=False)
        summary = runner.apply_classified("clean_label", {"label": label, "relabel": relabel}, cleanup_records(label))
        ws.store.event("job_run", f"clean_label {label}: {json.dumps(summary, ensure_ascii=False)}")
        return {"summary": summary, **cleanup_status(label)}

    # ------------------------------------------------------------ credentials

    def token_path() -> Path:
        return Path(os.environ.get("MAILMAN_TOKEN_FILE", ws.data / "token.json"))

    def secrets_path() -> Path:
        return Path(os.environ.get("MAILMAN_CLIENT_SECRETS", ws.data / "credentials.json"))

    @app.get("/api/credentials")
    def credentials():
        gmail_ok, detail = False, ""
        try:
            profile = gmail._execute(gmail.service().users().getProfile(userId="me"))
            gmail_ok, detail = True, profile.get("emailAddress", "")
        except Exception as e:
            detail = str(e)[:200]
        return {"gmail": {"connected": gmail_ok, "detail": detail, "token_file": str(token_path()),
                          "client_secrets_present": secrets_path().exists() or bool(
                              os.environ.get("GMAIL_CLIENT_ID") and os.environ.get("GMAIL_CLIENT_SECRET")),
                          "fixed_token": bool(os.environ.get("GMAIL_REFRESH_TOKEN")),
                          "client_secrets_file": str(secrets_path())},
                "typesafe": {"key_present": bool(os.environ.get("TYPESAFE_API_KEY"))},
                "can_import": bool(os.environ.get("MAILMAN_IMPORT"))}

    def oauth_flow(redirect_uri: str):
        """The sign-in, from the OAuth client file or, without one, from the client id and secret in the environment."""
        from google_auth_oauthlib.flow import Flow
        os.environ["OAUTHLIB_INSECURE_TRANSPORT"] = "1"   # the redirect goes to a local http address
        if secrets_path().exists():
            return Flow.from_client_secrets_file(str(secrets_path()), gmail.SCOPES, redirect_uri=redirect_uri)
        if os.environ.get("GMAIL_CLIENT_ID") and os.environ.get("GMAIL_CLIENT_SECRET"):
            return Flow.from_client_config({"installed": {
                "client_id": os.environ["GMAIL_CLIENT_ID"], "client_secret": os.environ["GMAIL_CLIENT_SECRET"],
                "auth_uri": "https://accounts.google.com/o/oauth2/auth", "token_uri": gmail.TOKEN_URI,
                "redirect_uris": ["http://localhost"]}}, gmail.SCOPES, redirect_uri=redirect_uri)
        raise HTTPException(400, "no Google OAuth client: set the client id and secret in the app's options, "
                                 f"or place the client file at {secrets_path()}")

    def store_token(flow) -> None:
        path = token_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(flow.credentials.to_json())
        os.chmod(path, 0o600)
        os.environ["MAILMAN_TOKEN_FILE"] = str(path)
        ws._svc = None
        ws.store.event("gmail_connected", "token stored")

    @app.post("/api/oauth/link")
    def oauth_link():
        """Sign-in without a browser on this machine, step 1: Google's consent address. Google then sends
        your browser to a localhost address that does not load; step 2 takes that address."""
        flow = oauth_flow(PASTE_REDIRECT)
        url, _ = flow.authorization_url(access_type="offline", prompt="consent")
        app.state.oauth_paste = flow
        return {"url": url, "lands_on": PASTE_REDIRECT}

    @app.post("/api/oauth/finish")
    def oauth_finish(url: str = Body(..., embed=True)):
        """Step 2: the address your browser ended on (http://localhost:…/?code=…)."""
        flow = getattr(app.state, "oauth_paste", None)
        if flow is None:
            raise HTTPException(400, "no sign-in in progress: open the Google link first")
        if "code=" not in url:
            raise HTTPException(400, "that address has no code in it: copy the whole address your browser ended on")
        try:
            flow.fetch_token(authorization_response=url.strip())
        except Exception as e:
            raise HTTPException(400, f"Google did not accept it: {str(e)[:200]}")
        store_token(flow)
        app.state.oauth_paste = None
        return credentials()

    @app.get("/api/oauth/start")
    def oauth_start(request: Request):
        """The web way: send the browser to Google's consent page; Google sends it back to /api/oauth/callback."""
        flow = oauth_flow(str(request.base_url) + "api/oauth/callback")
        url, state = flow.authorization_url(access_type="offline", prompt="consent")
        app.state.oauth = flow
        return RedirectResponse(url)

    @app.get("/api/oauth/callback")
    def oauth_callback(request: Request):
        flow = getattr(app.state, "oauth", None)
        if flow is None:
            raise HTTPException(400, "no sign-in in progress")
        flow.fetch_token(authorization_response=str(request.url))
        store_token(flow)
        return HTMLResponse("<p>Gmail is connected. You can close this tab and return to mailman.</p>"
                            "<script>setTimeout(()=>location.href='../../#/health',1200)</script>")

    @app.get("/api/export")
    def export_bundle(everything: bool = False):
        """Download this installation as one file: the configuration, or with `everything` also the log and the
        test set. Never the Gmail sign-in."""
        import tempfile
        import time as _time

        from starlette.background import BackgroundTask

        from mailman import bundle
        tmp = Path(tempfile.mkdtemp()) / "bundle.zip"
        bundle.write(tmp, ws.dir, ws.data, everything)
        name = f"mailman-{'full' if everything else 'config'}-{_time.strftime('%Y%m%d')}.zip"
        return FileResponse(tmp, media_type="application/zip", filename=name,
                            background=BackgroundTask(shutil.rmtree, tmp.parent, True))

    @app.post("/api/import")
    async def import_bundle(request: Request, part: int = 0, last: bool = True):
        """A bundle from Backup → Export or `mailman export`, sent in pieces: JSON {part, last, data: base64}, or
        (older pages) the raw bytes with part and last in the address. After the last piece the app stops,
        unpacks it over its configuration and data, and starts again; the Gmail sign-in is kept."""
        import base64
        import zipfile

        target = os.environ.get("MAILMAN_IMPORT")
        if not target:
            raise HTTPException(400, "importing works in the installed app only; here, copy the files yourself")
        body = await request.body()
        if "json" in request.headers.get("content-type", ""):
            try:
                sent = json.loads(body)
                part, last, body = int(sent["part"]), bool(sent["last"]), base64.b64decode(sent["data"])
            except Exception:
                raise HTTPException(400, "the upload was not understood; reload this page and try again")
        piece = Path(target + ".part")
        if part == 0 and piece.exists():
            piece.unlink()
        with open(piece, "ab") as out:
            out.write(body)
        if not last:
            return {"received": part}
        if not zipfile.is_zipfile(piece):
            piece.unlink()
            raise HTTPException(400, "that is not a mailman bundle")
        with zipfile.ZipFile(piece) as z:
            if not any(n.startswith("config/") for n in z.namelist()):
                piece.unlink()
                raise HTTPException(400, "that zip file has no config/ folder in it: not a mailman bundle")
        piece.rename(target)
        return {"restarting": True}

    app.mount("/static", StaticFiles(directory=STATIC), name="static")

    @app.get("/")
    def index():
        """The page as one document, script and stylesheet included, and never cached: a browser (or a proxy in
        front) then cannot pair a new page with an older script, which is what a separately cached file led to."""
        html = (STATIC / "index.html").read_text()
        css, js = (STATIC / "style.css").read_text(), (STATIC / "app.js").read_text()
        html = html.replace('<link rel="stylesheet" href="static/style.css">', f"<style>\n{css}\n</style>", 1)
        html = html.replace('<script src="static/app.js"></script>', "<script>\n" + js.replace("</script", "<\\/script") + "\n</script>", 1)
        html = html.replace("<body>", f'<body data-version="{VERSION}">', 1)
        return HTMLResponse(html, headers={"Cache-Control": "no-store, max-age=0", "Pragma": "no-cache"})

    return app
