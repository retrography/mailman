"""Home Assistant: show mailman's alerts there too. Only when running as a Home Assistant app (the
Supervisor then provides SUPERVISOR_TOKEN and the manifest has `homeassistant_api: true`); otherwise nothing.

  binary_sensor.mailman_problem   on while anything needs attention (device class: problem)
  sensor.mailman_waiting          emails that could not be handled and wait in the inbox
  a persistent notification       the alerts as text, replaced when they change, dismissed when they clear
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
import urllib.request

log = logging.getLogger("mailman.hass")
BASE = "http://supervisor/core/api"


def available() -> bool:
    return bool(os.environ.get("SUPERVISOR_TOKEN"))


def _post(path: str, body: dict) -> None:
    req = urllib.request.Request(BASE + path, data=json.dumps(body).encode(), method="POST", headers={
        "Authorization": f"Bearer {os.environ['SUPERVISOR_TOKEN']}", "Content-Type": "application/json"})
    urllib.request.urlopen(req, timeout=10).close()


def push(alerts: list[dict], waiting: int) -> None:
    _post("/states/binary_sensor.mailman_problem", {"state": "on" if alerts else "off", "attributes": {
        "friendly_name": "Mailman problem", "device_class": "problem", "problems": [a["title"] for a in alerts]}})
    _post("/states/sensor.mailman_waiting", {"state": waiting, "attributes": {
        "friendly_name": "Mailman emails waiting", "unit_of_measurement": "emails", "icon": "mdi:email-alert"}})
    if alerts:
        text = "\n\n".join(f"**{a['title']}**\n{a['detail']}\n{a['fix']}" for a in alerts)
        _post("/services/persistent_notification/create",
              {"notification_id": "mailman", "title": "Mailman needs attention", "message": text})
    else:
        _post("/services/persistent_notification/dismiss", {"notification_id": "mailman"})


def watch(health, every: int = 60) -> None:
    """In the background: push the alerts whenever they change, and again every half hour (Home Assistant
    forgets states set from outside when it restarts)."""
    if not available():
        return

    def loop() -> None:
        last, sent_at = None, 0.0
        while True:
            try:
                h = health()
                now = (json.dumps(h["alerts"], sort_keys=True), h["waiting"])
                if now != last or time.time() - sent_at > 1800:
                    push(h["alerts"], h["waiting"])
                    last, sent_at = now, time.time()
            except Exception as e:   # Home Assistant restarting, or our own database busy: try again next round
                log.warning("alerts not pushed to Home Assistant: %s", e)
            time.sleep(every)

    threading.Thread(target=loop, name="hass-alerts", daemon=True).start()
