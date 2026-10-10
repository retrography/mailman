"""Outlook.com (or Microsoft 365) access through Microsoft Graph, in Gmail's terms (see mailman.mailbox).

  a label              a category: flat names, several per message
  INBOX                the Inbox folder; taking INBOX off a message there moves it to Archive
  TRASH · SPAM         the Deleted Items and Junk Email folders
  UNREAD · STARRED     the read state and the follow-up flag
  a search             Gmail's words from: label: in: older_than: newer_than: and their negations; nothing else
  new mail             asked for every `outlook.poll_seconds` (Graph cannot push to a server without a public address)
  a label you add      noticed on mail in the inbox

Sign-in is the device-code flow: you open Microsoft's page on any device and type a short code. It needs your
own app registration (a public client that allows personal accounts): OUTLOOK_CLIENT_ID, and OUTLOOK_TENANT if
the mailbox is not a personal one (default: consumers). The sign-in is kept in MAILMAN_OUTLOOK_TOKEN_FILE
(default: token-outlook.json in the data folder).
"""

from __future__ import annotations

import base64
import email
import email.policy
import json
import os
import re
import time
from datetime import datetime, timedelta, timezone
from email.utils import formataddr
from itertools import islice
from pathlib import Path
from urllib.parse import quote

import requests

ROOT = Path(__file__).resolve().parents[2]
LOGIN = "https://login.microsoftonline.com"
GRAPH = "https://graph.microsoft.com/v1.0"
SCOPES = "offline_access User.Read Mail.ReadWrite"
FOLDERS = {"INBOX": "inbox", "TRASH": "deleteditems", "SPAM": "junkemail"}
OTHER = {"UNREAD", "STARRED", "IMPORTANT", "user_labels"}   # label words that are not categories
ONE = "id,conversationId,parentFolderId,categories,isRead,flag,bodyPreview,receivedDateTime"
FOUND = "id,from,subject,categories,parentFolderId,receivedDateTime"


class GraphError(Exception):
    def __init__(self, status_code: int, message: str):
        super().__init__(f"{status_code} {message}")
        self.status_code = status_code


# ------------------------------------------------------------ sign-in

def client_id() -> str:
    return os.environ.get("OUTLOOK_CLIENT_ID", "").strip()


def tenant() -> str:
    return os.environ.get("OUTLOOK_TENANT", "").strip() or "consumers"


def token_file() -> Path:
    return Path(os.environ.get("MAILMAN_OUTLOOK_TOKEN_FILE") or
                Path(os.environ.get("MAILMAN_DATA", ROOT / "data")) / "token-outlook.json")


def _token(tenant_: str, data: dict) -> dict:
    return requests.post(f"{LOGIN}/{tenant_}/oauth2/v2.0/token", data=data, timeout=30).json()


def _save(client: str, tenant_: str, refresh_token: str, signed_in: float) -> None:
    path = token_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps({"client_id": client, "tenant": tenant_, "refresh_token": refresh_token,
                               "signed_in": signed_in}))
    os.chmod(tmp, 0o600)
    tmp.replace(path)


def start_sign_in() -> dict:
    """Step 1: {user_code, verification_uri, device_code, interval, expires_in} — the page to open and the code
    to type there."""
    if not client_id():
        raise RuntimeError("no Microsoft app registration: set its client id (OUTLOOK_CLIENT_ID)")
    d = requests.post(f"{LOGIN}/{tenant()}/oauth2/v2.0/devicecode", data={"client_id": client_id(), "scope": SCOPES},
                      timeout=30).json()
    if "device_code" not in d:
        raise RuntimeError(f"Microsoft refused: {d.get('error_description') or d.get('error') or d}"[:300])
    return d


def finish_sign_in(device_code: str) -> bool:
    """Step 2, asked every few seconds: True once you have approved (the sign-in is stored), False until then."""
    d = _token(tenant(), {"grant_type": "urn:ietf:params:oauth:grant-type:device_code", "client_id": client_id(),
                          "device_code": device_code})
    if d.get("error") in ("authorization_pending", "slow_down"):
        return False
    if "refresh_token" not in d:
        raise RuntimeError(f"Microsoft refused: {d.get('error_description') or d.get('error') or d}"[:300])
    _save(client_id(), tenant(), d["refresh_token"], time.time())
    return True


class Graph:
    """Calls to Microsoft Graph as the signed-in account, always with immutable ids (a message keeps its id
    when it moves to another folder)."""

    def __init__(self):
        self.http = requests.Session()
        self.access, self.expires = "", 0.0

    def token(self) -> str:
        if time.time() < self.expires - 120:
            return self.access
        try:
            saved = json.loads(token_file().read_text())   # read each time: another process may have renewed it
        except (OSError, ValueError):
            raise RuntimeError("Outlook is not connected: no sign-in has been stored yet") from None
        d = _token(saved["tenant"], {"grant_type": "refresh_token", "client_id": saved["client_id"],
                                     "refresh_token": saved["refresh_token"], "scope": SCOPES})
        if "access_token" not in d:
            raise RuntimeError(f"Outlook sign-in refused: {d.get('error')}: {d.get('error_description')}"[:300])
        if d.get("refresh_token") and d["refresh_token"] != saved["refresh_token"]:   # Microsoft hands out a newer one
            _save(saved["client_id"], saved["tenant"], d["refresh_token"], saved.get("signed_in") or time.time())
        self.access, self.expires = d["access_token"], time.time() + int(d.get("expires_in", 3600))
        return self.access

    def send(self, method: str, url: str, *, params: dict | None = None, body: dict | None = None,
             prefer: str = "", retries: int = 6) -> requests.Response:
        url = url if url.startswith("http") else GRAPH + url
        for attempt in range(retries):
            r = self.http.request(method, url, params=params, json=body, timeout=60, headers={
                "Authorization": f"Bearer {self.token()}",
                "Prefer": 'IdType="ImmutableId"' + (f", {prefer}" if prefer else "")})
            if r.status_code == 401 and attempt == 0:
                self.expires = 0.0
                continue
            if r.status_code in (429, 500, 502, 503, 504) and attempt < retries - 1:
                wait = r.headers.get("Retry-After", "")
                time.sleep(min(float(wait) if wait.isdigit() else 2 ** attempt, 60))
                continue
            if r.status_code >= 400:
                try:
                    message = r.json()["error"]["message"]
                except Exception:
                    message = r.text
                raise GraphError(r.status_code, f"{message[:300]} ({method} {url.split('?')[0][len(GRAPH):]})")
            return r
        raise AssertionError("unreachable")

    def call(self, method: str, url: str, **how) -> dict:
        r = self.send(method, url, **how)
        return r.json() if r.content else {}

    def pages(self, url: str, params: dict | None = None):
        while url:
            res = self.call("GET", url, params=params)
            yield from res.get("value", [])
            url, params = res.get("@odata.nextLink"), None


# ------------------------------------------------------------ a message in the Gmail API's shape

def _part(p) -> dict:
    headers = []
    for k, v in p.items():
        try:
            headers.append({"name": k, "value": str(v)})
        except Exception:   # a header the parser cannot make sense of
            pass
    out = {"mimeType": p.get_content_type(), "headers": headers, "body": {}}
    if p.is_multipart():
        out["parts"] = [_part(s) for s in p.iter_parts()]
    elif p.get_content_maintype() == "text" and p.get_content_disposition() != "attachment":
        try:
            text = p.get_content()
        except (LookupError, UnicodeError, ValueError):   # an unknown or wrong character set
            text = (p.get_payload(decode=True) or b"").decode("utf-8", "replace")
        out["body"]["data"] = base64.urlsafe_b64encode(text.encode()).decode()
    return out


def payload(mime: bytes) -> dict:
    """The message as Gmail's `payload`: decoded headers, and the text parts in base64."""
    return _part(email.message_from_bytes(mime, policy=email.policy.default))


# ------------------------------------------------------------ searches

TERM = re.compile(r'(?<!\S)(-?)(from|label|in|older_than|newer_than):("[^"]*"|\S*)')
AGE = re.compile(r"(\d+)([dmy])")
DAYS = {"d": 1, "m": 30, "y": 365}


def parse_search(q: str) -> list[tuple[bool, str, str]]:
    """A Gmail search as (negated, word, value) terms; ValueError for anything Outlook cannot answer."""
    terms = [(bool(neg), k, v.strip('"')) for neg, k, v in TERM.findall(q)]
    rest = TERM.sub("", q).strip()
    if rest:
        raise ValueError(f"an Outlook mailbox cannot search for “{rest}”: it understands from: label: in: "
                         f"older_than: newer_than: and their negations")
    for _, k, v in terms:
        if k == "in" and v not in ("inbox", "trash", "spam", "anywhere"):
            raise ValueError(f"an Outlook mailbox cannot search in:{v} (inbox, trash, spam or anywhere)")
        if k in ("older_than", "newer_than") and not AGE.fullmatch(v):
            raise ValueError(f"{k}:{v} is not an age such as 7d, 2m or 1y")
    return terms


def same_label(a: str, b: str) -> bool:
    """Gmail's search writes a label without capitals and with dashes for spaces."""
    return a.lower().replace("-", " ") == b.lower().replace("-", " ")


# ------------------------------------------------------------ the mailbox

class OutlookBox:
    name, title = "outlook", "Outlook"

    def __init__(self, settings: dict | None = None):
        s = (settings or {}).get("outlook") or {}
        self.poll = s.get("poll_seconds", 60)
        self.archive = s.get("archive_folder", "archive")
        self.g = Graph()
        self.folders: dict[str, str] = {}   # folder ids by well-known name
        self.noticed: set[str] = set()      # inbox mail whose watched label was already reported

    def folder_id(self, name: str) -> str:
        if name not in self.folders:
            self.folders[name] = self.g.call("GET", f"/me/mailFolders/{name}", params={"$select": "id"})["id"]
        return self.folders[name]

    def address(self) -> str:
        me = self.g.call("GET", "/me", params={"$select": "mail,userPrincipalName"})
        return (me.get("mail") or me["userPrincipalName"]).lower()

    def label_id(self, name: str) -> str:
        return name

    def labels(self) -> list[str]:
        return []   # the list of categories is a mailbox setting, which this sign-in does not ask to read

    def _labels(self, m: dict) -> list[str]:
        out = [label for label, folder in FOLDERS.items() if m.get("parentFolderId") == self.folder_id(folder)]
        if m.get("isRead") is False:
            out.append("UNREAD")
        if (m.get("flag") or {}).get("flagStatus") == "flagged":
            out.append("STARRED")
        return out + list(m.get("categories") or [])

    def get(self, msg_id: str) -> dict:
        path = f"/me/messages/{quote(msg_id, safe='')}"
        m = self.g.call("GET", path, params={"$select": ONE})
        mime = self.g.send("GET", path + "/$value").content
        received = datetime.fromisoformat(m["receivedDateTime"]) if m.get("receivedDateTime") else None
        return {"id": m["id"], "threadId": m.get("conversationId") or "", "labelIds": self._labels(m),
                "snippet": m.get("bodyPreview") or "", "payload": payload(mime),
                "internalDate": str(int(received.timestamp() * 1000)) if received else "0"}

    def headers(self, msg_id: str) -> dict[str, str]:
        m = self.g.call("GET", f"/me/messages/{quote(msg_id, safe='')}", params={"$select": "from,subject"})
        sender = (m.get("from") or {}).get("emailAddress") or {}
        return {"from": formataddr((sender.get("name") or "", sender.get("address") or "")),
                "subject": m.get("subject") or ""}

    # ------------------------------------------------------------ searches

    def _find(self, q: str, include_trash: bool = False):
        """The messages a Gmail search finds. Graph narrows by one term (the sender, else the label, else the
        folder); every term is then checked here, so the answer does not depend on how Graph matched."""
        terms = parse_search(q)
        if any(not v for neg, _, v in terms if not neg):   # e.g. from: with no sender: nothing matches
            return
        first = lambda word: next((v for neg, k, v in terms if k == word and not neg), None)
        sender, label, place = first("from"), first("label"), first("in")
        url = f"/me/mailFolders/{FOLDERS[place.upper()]}/messages" if place in ("inbox", "trash", "spam") else "/me/messages"
        params = {"$select": FOUND, "$top": 100}
        if sender:
            params["$search"] = f'"from:{sender}"'
        elif label:
            params["$filter"] = "categories/any(c:c eq '%s')" % label.replace("'", "''")
        everywhere = include_trash or place in ("trash", "spam", "anywhere")
        hidden = () if everywhere else (self.folder_id("deleteditems"), self.folder_id("junkemail"))
        now = datetime.now(timezone.utc)
        for m in self.g.pages(url, params):
            if m.get("parentFolderId") not in hidden and all(self._holds(m, k, v, now) != neg for neg, k, v in terms):
                yield m

    def _holds(self, m: dict, word: str, value: str, now: datetime) -> bool:
        if word == "from":
            address = ((m.get("from") or {}).get("emailAddress") or {}).get("address", "").lower()
            value = value.lower()
            return address == value or address.endswith(("@" + value, "." + value))
        if word == "label":
            return any(same_label(value, c) for c in m.get("categories") or [])
        if word == "in":
            return value == "anywhere" or m.get("parentFolderId") == self.folder_id(FOLDERS[value.upper()])
        n, unit = AGE.fullmatch(value).groups()
        received = datetime.fromisoformat(m["receivedDateTime"]) if m.get("receivedDateTime") else now
        return (received < now - timedelta(days=int(n) * DAYS[unit])) == (word == "older_than")

    def search(self, q: str, include_trash: bool = False, limit: int | None = None) -> list[str]:
        found = list(islice(self._find(q, include_trash), limit or None))
        return [m["id"] for m in sorted(found, key=lambda m: m.get("receivedDateTime") or "", reverse=True)]

    def count(self, q: str, limit: int, exclude: str | None = None) -> int:
        return sum(1 for m in islice(self._find(q), limit) if m["id"] != exclude)

    def in_label(self, label: str) -> list[str]:
        return self.search(f'label:"{label}"', include_trash=True)

    # ------------------------------------------------------------ outcomes

    def apply(self, ids: list[str], trash: bool, add: list[str], remove: list[str], batch: bool = False) -> None:
        for i in ids:
            self._apply(i, trash, add, remove)

    def _apply(self, msg_id: str, trash: bool, add: list[str], remove: list[str]) -> None:
        path = f"/me/messages/{quote(msg_id, safe='')}"
        put = [n for n in add if n not in FOLDERS and n not in OTHER]
        take = [n for n in remove if n not in FOLDERS and n not in OTHER]
        clear = "user_labels" in remove
        # where it goes: a folder it is sent to, else Archive when it has to leave the folder it is in
        to = "deleteditems" if trash or "TRASH" in add else "junkemail" if "SPAM" in add else \
            "inbox" if "INBOX" in add else None
        leaves = [FOLDERS[n] for n in FOLDERS if n in remove]
        now = {}
        if ((put or take) and not clear) or (to is None and leaves):
            now = self.g.call("GET", path, params={"$select": "categories,parentFolderId"})
        change: dict = {}
        if clear:
            change["categories"] = put
        elif put or take:
            have = list(now.get("categories") or [])
            new = [c for c in have if c not in take] + [c for c in put if c not in have]
            if new != have:
                change["categories"] = new
        if "UNREAD" in remove or "UNREAD" in add:
            change["isRead"] = "UNREAD" not in add
        if "STARRED" in add or "STARRED" in remove:
            change["flag"] = {"flagStatus": "flagged" if "STARRED" in add else "notFlagged"}
        if to is None and now.get("parentFolderId") in [self.folder_id(f) for f in leaves]:
            to = self.archive
        if change:
            self.g.call("PATCH", path, body=change)
        if to:
            self.g.call("POST", path + "/move", body={"destinationId": to})

    # ------------------------------------------------------------ what changed

    def checkpoint(self) -> str:
        """Where the inbox stands now: the address that answers with what arrived after this moment."""
        return self._arrivals("/me/mailFolders/inbox/messages/delta", {"changeType": "created", "$select": "id"})[1]

    def _arrivals(self, url: str, params: dict | None = None) -> tuple[list[str], str]:
        ids: list[str] = []
        while True:
            res = self.g.call("GET", url, params=params, prefer="odata.maxpagesize=200")
            ids += [m["id"] for m in res.get("value", []) if "@removed" not in m]
            if "@odata.deltaLink" in res:
                return ids, res["@odata.deltaLink"]
            url, params = res["@odata.nextLink"], None

    def changes(self, start: str, watch: list[str] = ()) -> tuple[list[dict], list[dict], str]:
        """(messages that arrived in the inbox, inbox mail now carrying a label of `watch`, new checkpoint)."""
        from mailman.mailbox import Expired
        try:
            ids, latest = self._arrivals(start)
        except GraphError as e:
            if e.status_code == 410:
                raise Expired(start) from e
            raise
        labelled = []
        for label in watch:
            if label in FOLDERS or label in OTHER:
                continue
            for i in self.search(f'in:inbox label:"{label}"'):
                if i not in self.noticed:
                    self.noticed.add(i)
                    labelled.append({"id": i, "labels": [label]})
        return [{"id": i, "labels": ["INBOX"]} for i in ids], labelled, latest

    def waiter(self, renew: int) -> "Poll":
        return Poll(self.poll)


class Poll:
    """Nothing tells this server that Outlook mail arrived: the wait simply ends after `every` seconds."""

    def __init__(self, every: int):
        self.every = every

    def arm(self) -> bool:
        return False

    def wait(self, seconds: float, stopping) -> None:
        deadline = time.time() + min(seconds, self.every)
        while time.time() < deadline and not stopping():
            time.sleep(1)

    def alive(self) -> bool:
        return True

    def close(self) -> None:
        pass
