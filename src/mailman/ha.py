"""Entry point of the container (Home Assistant app, or any container): reads the options, prepares the
storage, and keeps the web interface and one daemon per signed-in mailbox running.

Storage under Home Assistant:
  /homeassistant/mailman/   the configuration files (Home Assistant's configuration folder, part of its backups);
                            seeded from the image on first start, yours from then on
  /data/                    the app's private folder, which no other app can read, part of the app's own backup:
                            the log database, the test set, caches, and the mailbox sign-ins (tokens.json)
Any other container: config/ and data/ in MAILMAN_HOME (default /config).

Before 0.11.0 everything was in the app's own folder (/config, visible as /addon_configs/<app>). The first start
of a newer version copies it to the new places, checks every file, and leaves the old folder as it was.

Options (Home Assistant: /data/options.json): typesafe_api_key; optionally gmail_client_id, gmail_client_secret
and gmail_refresh_token. Without a refresh token, sign in from the interface (Health → Connect Gmail).
For an Outlook mailbox as well: outlook_client_id (and outlook_tenant), then Health → Connect Outlook.
"""

from __future__ import annotations

import filecmp
import json
import logging
import os
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

from mailman import jsonfile, tokens

log = logging.getLogger("mailman.ha")
OPTIONS = Path("/data/options.json")
PRIVATE = OPTIONS.parent
LEGACY = Path("/config")   # the app's own folder (addon_config): where versions before 0.11.0 kept everything
SHARED = Path("/homeassistant")   # Home Assistant's configuration folder (homeassistant_config)
DEFAULTS = Path(os.environ.get("MAILMAN_DEFAULTS", "/app/defaults/config"))
ENV = {"typesafe_api_key": "TYPESAFE_API_KEY", "gmail_client_id": "GMAIL_CLIENT_ID",
       "gmail_client_secret": "GMAIL_CLIENT_SECRET", "gmail_refresh_token": "GMAIL_REFRESH_TOKEN",
       "outlook_client_id": "OUTLOOK_CLIENT_ID", "outlook_tenant": "OUTLOOK_TENANT"}


def places() -> tuple[Path, Path]:
    """The configuration folder and the data folder."""
    if os.environ.get("MAILMAN_HOME") or not SHARED.is_dir():
        home = Path(os.environ.get("MAILMAN_HOME") or LEGACY)
        return home / "config", home / "data"
    return SHARED / "mailman", PRIVATE


def move_in(config: Path, data: Path) -> None:
    """Copy config/ and data/ from where versions before 0.11.0 kept them, the first time. Every file is compared
    with its copy before the copies are used; the old folder is left as it was, with a note. Stops the app rather
    than start on a fresh configuration if it cannot."""
    old = LEGACY / "config"
    if config == old or not old.is_dir() or (LEGACY / "MOVED.txt").exists() or any(config.glob("*.yaml")):
        return   # nothing to move, or moved before (an old copy must never overwrite newer data)
    if config.exists() and any(config.iterdir()):
        raise SystemExit(f"{config} has files but no configuration: move them away, so that {old} can be moved there")
    # the data first: the configuration folder appearing is what marks the move as done
    if (LEGACY / "data").is_dir():
        shutil.copytree(LEGACY / "data", data, symlinks=True, dirs_exist_ok=True)
    staging = config.with_name(config.name + ".moving")
    shutil.rmtree(staging, ignore_errors=True)
    shutil.copytree(old, staging, symlinks=True)
    pairs = [(f, staging / f.relative_to(old)) for f in old.rglob("*") if f.is_file()]
    pairs += [(f, data / f.relative_to(LEGACY / "data")) for f in (LEGACY / "data").rglob("*") if f.is_file()]
    differ = [str(a.relative_to(LEGACY)) for a, b in pairs if not filecmp.cmp(a, b, shallow=False)]
    if differ:
        raise SystemExit(f"copying {LEGACY} went wrong ({', '.join(differ[:3])}); it is left as it was")
    if config.exists():
        config.rmdir()
    staging.rename(config)
    (LEGACY / "MOVED.txt").write_text(
        f"Mailman 0.11.0 and later keep the configuration in Home Assistant's configuration folder ({config.name}/\n"
        "there) and the data and the mailbox sign-ins in the app's private folder. Everything here was copied\n"
        f"there on {time.strftime('%Y-%m-%d %H:%M')} and checked file by file; it is not used any more and can be\n"
        "deleted. An older version of the app would still start from what is here.\n")
    log.info("moved %d files from %s to %s and %s", len(pairs), LEGACY, config, data)


def merge_indexes(cache: Path) -> list[str]:
    """Before 0.11.0 each mailbox kept a label's sender index in a file of its own: <label>_index.json for Gmail,
    outlook_<label>_index.json for Outlook. Now one file holds every mailbox's: {mailbox: {message id: …}}."""
    names = {f.name.removeprefix("outlook_") for f in cache.glob("*_index.json")} - {"sample_index.json"}
    for name in sorted(names):
        outlook = cache / f"outlook_{name}"
        with jsonfile.edit(cache / name) as index:
            if index and not set(index) <= {"gmail", "outlook"}:   # Gmail's own, from before
                gmail = dict(index)
                index.clear()
                index["gmail"] = gmail
            if outlook.exists():
                mine = index.setdefault("outlook", {})
                for mid, entry in json.loads(outlook.read_text()).items():
                    mine.setdefault(mid, entry)
        outlook.unlink(missing_ok=True)
    return sorted(names)


def prepare(config: Path, data: Path) -> dict[str, str]:
    """The environment both processes run in."""
    data.mkdir(parents=True, exist_ok=True)
    if not any(config.glob("*.yaml")):   # first start: the configuration shipped with the image becomes yours
        shutil.copytree(DEFAULTS, config, dirs_exist_ok=True)
        log.info("first start: configuration copied to %s", config)
    for missing in sorted(p.name for p in DEFAULTS.glob("*.yaml") if not (config / p.name).exists()):
        shutil.copy(DEFAULTS / missing, config / missing)   # a file a newer version added
        log.info("added the new configuration file %s", missing)
    if moved := tokens.adopt(data, data / "tokens.json"):
        log.info("the %s sign-in moved into %s", " and ".join(moved), data / "tokens.json")
    merge_indexes(data / "cache")
    env = dict(os.environ, MAILMAN_CONFIG=str(config), MAILMAN_DATA=str(data),
               MAILMAN_TOKENS=str(data / "tokens.json"), MAILMAN_CLIENT_SECRETS=str(data / "credentials.json"))
    options = json.loads(OPTIONS.read_text()) if OPTIONS.exists() else {}
    for key, name in ENV.items():
        if str(options.get(key) or "").strip():
            env[name] = str(options[key]).strip()
    if os.environ.get("SUPERVISOR_TOKEN"):   # under Home Assistant only its Ingress proxy may reach the interface
        env.setdefault("MAILMAN_ALLOW_FROM", "172.30.32.2")
    return env


def take_import(config: Path, data: Path) -> bool:
    """Unpack <data>/import.zip (made with `mailman export`): its config/ and data/ replace what is here. The
    mailbox sign-ins are never part of a bundle and are kept."""
    import zipfile

    bundle = data / "import.zip"
    if not bundle.exists():
        return False
    try:
        with zipfile.ZipFile(bundle) as z:
            names = [n for n in z.namelist() if not n.endswith("/")]
            bad = [n for n in names if n.startswith("/") or ".." in Path(n).parts or Path(n).parts[0] not in ("config", "data")
                   or Path(n).name in ("tokens.json", *tokens.OLD.values(), "credentials.json", OPTIONS.name)]
            if bad or not any(n.startswith("config/") for n in names):
                raise ValueError(f"not a mailman bundle ({bad[:3] or 'no config/ inside'})")
            for n in names:
                top, *rest = Path(n).parts
                target = (config if top == "config" else data).joinpath(*rest)
                target.parent.mkdir(parents=True, exist_ok=True)
                with z.open(n) as src, open(target, "wb") as out:
                    shutil.copyfileobj(src, out)
        log.info("imported %d files from %s", len(names), bundle.name)
        bundle.unlink()
    except Exception as e:   # leave what is here untouched; keep the file aside so it is not tried again
        log.error("import failed: %s", e)
        bundle.rename(data / "import.failed.zip")
    return True


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    config, data = places()
    move_in(config, data)   # before an import, which would otherwise make the configuration here first
    config.mkdir(parents=True, exist_ok=True)
    data.mkdir(parents=True, exist_ok=True)
    take_import(config, data)
    env = prepare(config, data)
    env["MAILMAN_IMPORT"] = str(data / "import.zip")
    os.environ.update(env)   # mailbox.signed_in looks where the daemons will
    from mailman import mailbox

    port = os.environ.get("MAILMAN_PORT", "8099")
    exe = [sys.executable, "-m", "mailman.cli"]
    stopping = False

    def stop(*_):
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    def start_web():
        return subprocess.Popen([*exe, "web", "--host", "0.0.0.0", "--port", port], env=env)

    def fresh() -> dict[str, dict]:
        return {a: {"proc": None, "wait": 5, "started": 0.0, "seen": None} for a in mailbox.ACCOUNTS}

    web = start_web()
    daemons = fresh()   # per mailbox: its daemon, and the sign-in that daemon was started with

    while not stopping:
        if web.poll() is not None:   # the interface died: leave, so the container is restarted as a whole
            log.error("the web interface stopped (exit %s)", web.returncode)
            break
        if (data / "import.zip").exists():   # a bundle was uploaded: stop everything, unpack, start again
            time.sleep(1)                    # let the upload's answer leave
            for p in (*(d["proc"] for d in daemons.values()), web):
                if p is not None and p.poll() is None:
                    p.terminate()
                    p.wait(timeout=60)
            take_import(config, data)
            web, daemons = start_web(), fresh()
            continue
        for account, d in daemons.items():
            now, daemon = mailbox.signed_in(account), d["proc"]
            if daemon is not None and daemon.poll() is None and now != d["seen"]:   # you signed in again: use the new sign-in
                log.info("the %s sign-in changed: restarting its daemon", mailbox.TITLES[account])
                daemon.terminate()
                daemon.wait(timeout=60)
            if daemon is None or daemon.poll() is not None:
                if daemon is not None and time.time() - d["started"] > 300:
                    d["wait"] = 5   # it ran for a while: not a start-up failure
                if now is None:
                    d["wait"] = 15   # nothing to do until you connect this mailbox in the interface
                elif time.time() - d["started"] >= d["wait"]:
                    if daemon is not None:
                        log.warning("the %s daemon stopped (exit %s); starting it again", account, daemon.returncode)
                        d["wait"] = min(d["wait"] * 2, 300)
                    d.update(proc=subprocess.Popen([*exe, "daemon", "--account", account], env=env),
                             started=time.time(), seen=now)
        time.sleep(2)

    procs = [*(d["proc"] for d in daemons.values()), web]
    for p in procs:
        if p is not None and p.poll() is None:
            p.terminate()
    for p in procs:
        if p is not None:
            try:
                p.wait(timeout=60)
            except subprocess.TimeoutExpired:
                p.kill()
    sys.exit(0 if stopping else 1)


if __name__ == "__main__":
    main()
