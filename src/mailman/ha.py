"""Entry point of the container (Home Assistant app, or any container): reads the options, prepares the
storage, and keeps the daemon and the web interface running.

Storage (MAILMAN_HOME, default /config — the app's own folder, visible as /addon_configs/<app> and in backups):
  config/   the configuration files; seeded from the image on first start, yours from then on
  data/     the log database, the test set, caches, and the Gmail sign-in

Options (Home Assistant: /data/options.json): typesafe_api_key; optionally gmail_client_id, gmail_client_secret
and gmail_refresh_token. Without a refresh token, sign in from the interface (Health → Connect Gmail).
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

log = logging.getLogger("mailman.ha")
OPTIONS = Path("/data/options.json")
DEFAULTS = Path(os.environ.get("MAILMAN_DEFAULTS", "/app/defaults/config"))
ENV = {"typesafe_api_key": "TYPESAFE_API_KEY", "gmail_client_id": "GMAIL_CLIENT_ID",
       "gmail_client_secret": "GMAIL_CLIENT_SECRET", "gmail_refresh_token": "GMAIL_REFRESH_TOKEN"}


def prepare() -> dict[str, str]:
    """The environment both processes run in."""
    home = Path(os.environ.get("MAILMAN_HOME", "/config"))
    config, data = home / "config", home / "data"
    data.mkdir(parents=True, exist_ok=True)
    if not config.exists():   # first start: the configuration shipped with the image becomes yours
        shutil.copytree(DEFAULTS, config)
        log.info("first start: configuration copied to %s", config)
    for missing in sorted(p.name for p in DEFAULTS.glob("*.yaml") if not (config / p.name).exists()):
        shutil.copy(DEFAULTS / missing, config / missing)   # a file a newer version added
        log.info("added the new configuration file %s", missing)
    env = dict(os.environ, MAILMAN_CONFIG=str(config), MAILMAN_DATA=str(data),
               MAILMAN_TOKEN_FILE=str(data / "token.json"), MAILMAN_CLIENT_SECRETS=str(data / "credentials.json"))
    options = json.loads(OPTIONS.read_text()) if OPTIONS.exists() else {}
    for key, name in ENV.items():
        if str(options.get(key) or "").strip():
            env[name] = str(options[key]).strip()
    if os.environ.get("SUPERVISOR_TOKEN"):   # under Home Assistant only its Ingress proxy may reach the interface
        env.setdefault("MAILMAN_ALLOW_FROM", "172.30.32.2")
    return env


def take_import(home: Path) -> bool:
    """Unpack <home>/import.zip (made with `mailman export`): its config/ and data/ replace what is here. The Gmail
    sign-in is never part of a bundle and is kept."""
    import zipfile

    bundle = home / "import.zip"
    if not bundle.exists():
        return False
    try:
        with zipfile.ZipFile(bundle) as z:
            names = [n for n in z.namelist() if not n.endswith("/")]
            bad = [n for n in names if n.startswith("/") or ".." in Path(n).parts or Path(n).parts[0] not in ("config", "data")
                   or Path(n).name in ("token.json", "credentials.json")]
            if bad or not any(n.startswith("config/") for n in names):
                raise ValueError(f"not a mailman bundle ({bad[:3] or 'no config/ inside'})")
            for n in names:
                target = home / n
                target.parent.mkdir(parents=True, exist_ok=True)
                with z.open(n) as src, open(target, "wb") as out:
                    shutil.copyfileobj(src, out)
        log.info("imported %d files from %s", len(names), bundle.name)
        bundle.unlink()
    except Exception as e:   # leave what is here untouched; keep the file aside so it is not tried again
        log.error("import failed: %s", e)
        bundle.rename(home / "import.failed.zip")
    return True


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    home = Path(os.environ.get("MAILMAN_HOME", "/config"))
    home.mkdir(parents=True, exist_ok=True)
    take_import(home)
    env = prepare()
    env["MAILMAN_IMPORT"] = str(home / "import.zip")
    port = os.environ.get("MAILMAN_PORT", "8099")
    exe = [sys.executable, "-m", "mailman.cli"]
    token = Path(env["MAILMAN_TOKEN_FILE"])
    stopping = False

    def stop(*_):
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    def start_web():
        return subprocess.Popen([*exe, "web", "--host", "0.0.0.0", "--port", port], env=env)

    web = start_web()
    daemon, wait, started, seen = None, 5, 0.0, None

    def signed_in() -> float | None:
        return token.stat().st_mtime if token.exists() else (1.0 if env.get("GMAIL_REFRESH_TOKEN") else None)

    while not stopping:
        if web.poll() is not None:   # the interface died: leave, so the container is restarted as a whole
            log.error("the web interface stopped (exit %s)", web.returncode)
            break
        if (home / "import.zip").exists():   # a bundle was uploaded: stop both, unpack, start again
            time.sleep(1)                    # let the upload's answer leave
            for p in (daemon, web):
                if p is not None and p.poll() is None:
                    p.terminate()
                    p.wait(timeout=60)
            take_import(home)
            web, daemon, started, wait = start_web(), None, 0.0, 5
            continue
        now = signed_in()
        if daemon is not None and daemon.poll() is None and now != seen:   # you signed in again: use the new sign-in
            log.info("the Gmail sign-in changed: restarting the daemon")
            daemon.terminate()
            daemon.wait(timeout=60)
        if daemon is None or daemon.poll() is not None:
            if daemon is not None and time.time() - started > 300:
                wait = 5   # it ran for a while: not a start-up failure
            if now is None:
                wait = 15   # nothing to do until you connect Gmail in the interface
            elif time.time() - started >= wait:
                if daemon is not None:
                    log.warning("the daemon stopped (exit %s); starting it again", daemon.returncode)
                    wait = min(wait * 2, 300)
                daemon, started, seen = subprocess.Popen([*exe, "daemon"], env=env), time.time(), now
        time.sleep(2)

    for p in (daemon, web):
        if p is not None and p.poll() is None:
            p.terminate()
    for p in (daemon, web):
        if p is not None:
            try:
                p.wait(timeout=60)
            except subprocess.TimeoutExpired:
                p.kill()
    sys.exit(0 if stopping else 1)


if __name__ == "__main__":
    main()
