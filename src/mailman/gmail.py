"""Gmail access. Credentials come from env vars (routine) or a local token file (dev).

Env (routine): GMAIL_CLIENT_ID, GMAIL_CLIENT_SECRET, GMAIL_REFRESH_TOKEN.
Dev fallback:  MAILMAN_TOKEN_FILE → an authorized-user token.json.
"""

from __future__ import annotations

import base64
import os
import re
import time
from dataclasses import dataclass, field

from bs4 import BeautifulSoup
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

# Full mail scope: covers the Gmail API and IMAP (the daemon's IDLE connection needs IMAP).
SCOPES = ["https://mail.google.com/"]
TOKEN_URI = "https://oauth2.googleapis.com/token"


def credentials() -> Credentials:
    if os.environ.get("GMAIL_REFRESH_TOKEN"):
        creds = Credentials(
            token=None,
            refresh_token=os.environ["GMAIL_REFRESH_TOKEN"],
            client_id=os.environ["GMAIL_CLIENT_ID"],
            client_secret=os.environ["GMAIL_CLIENT_SECRET"],
            token_uri=TOKEN_URI,
            scopes=SCOPES,
        )
    elif (path := os.environ.get("MAILMAN_TOKEN_FILE")) and os.path.exists(path):
        creds = Credentials.from_authorized_user_file(path)
    else:
        raise RuntimeError("Gmail is not connected: no sign-in has been stored yet")
    if not creds.valid:
        creds.refresh(Request())
    return creds


def service():
    return build("gmail", "v1", credentials=credentials(), cache_discovery=False)


@dataclass
class Message:
    id: str
    thread_id: str
    label_ids: list[str]
    headers: dict[str, str]  # lower-cased names; repeated headers joined with "\n"
    body_text: str
    body_html: str
    snippet: str
    internal_date: int
    links: list[str] = field(default_factory=list)

    def h(self, name: str) -> str:
        return self.headers.get(name.lower(), "")


def _decode(data: str) -> str:
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4)).decode("utf-8", "replace")


def _walk(part: dict, plain: list[str], html: list[str]) -> None:
    mime = part.get("mimeType", "")
    data = part.get("body", {}).get("data")
    if data and mime == "text/plain":
        plain.append(_decode(data))
    elif data and mime == "text/html":
        html.append(_decode(data))
    for sub in part.get("parts", []) or []:
        _walk(sub, plain, html)


_TAG_RE = re.compile(r"<(?:html|head|body|table|div|p|br|span|td|style)\b", re.I)


def html_to_text(html: str) -> tuple[str, list[str]]:
    """Visible text + link targets; drops <style>/<script>/<head> content."""
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["style", "script", "head", "title", "meta"]):
        tag.decompose()
    links = [a["href"] for a in soup.find_all("a", href=True)]
    return soup.get_text("\n"), links


def parse(raw: dict, html_tags_in_plain: int = 3, stub_ratio: int = 3, stub_min_words: int = 60) -> Message:
    """A Gmail message as text. The plain part is used unless it holds raw HTML (more than
    `html_tags_in_plain` tags) or is a stub next to a much longer HTML part (`stub_ratio`, `stub_min_words`)."""
    payload = raw.get("payload", {})
    headers: dict[str, str] = {}
    for h in payload.get("headers", []):
        k = h["name"].lower()
        headers[k] = f"{headers[k]}\n{h['value']}" if k in headers else h["value"]
    plain: list[str] = []
    html: list[str] = []
    _walk(payload, plain, html)
    body_html = "\n".join(html)
    html_text, links = html_to_text(body_html) if body_html else ("", [])
    plain_text = "\n".join(plain)
    # Some senders put raw HTML in the text/plain part; treat it as HTML.
    if len(_TAG_RE.findall(plain_text)) > html_tags_in_plain:
        plain_text, plain_links = html_to_text(plain_text)
        links = links or plain_links
    # Some senders ship a stub as text/plain ("view full message") and the real content only as HTML.
    stub = len(html_text.split()) > stub_ratio * len(plain_text.split()) and len(html_text.split()) > stub_min_words
    body_text = plain_text if plain_text.strip() and not stub else html_text
    return Message(
        id=raw["id"],
        thread_id=raw.get("threadId", ""),
        label_ids=raw.get("labelIds", []),
        headers=headers,
        body_text=body_text,
        body_html=body_html,
        snippet=raw.get("snippet", ""),
        internal_date=int(raw.get("internalDate", 0)),
        links=links,
    )


def get(svc, msg_id: str, retries: int = 6) -> dict:
    for attempt in range(retries):
        try:
            return svc.users().messages().get(userId="me", id=msg_id, format="full").execute()
        except HttpError as e:
            if e.status_code not in (403, 429, 500, 503) or attempt == retries - 1:
                raise
            if e.status_code == 403 and "rateLimitExceeded" not in str(e):
                raise
            time.sleep(2 ** attempt)
    raise AssertionError("unreachable")


def _execute(req, retries: int = 6):
    for attempt in range(retries):
        try:
            return req.execute()
        except HttpError as e:
            if e.status_code in (403, 429, 500, 503) and attempt < retries - 1 and \
                    (e.status_code != 403 or "ateLimit" in str(e)):
                time.sleep(2 ** attempt)
                continue
            raise
    raise AssertionError("unreachable")


def history_id(svc) -> str:
    """The mailbox's current history checkpoint."""
    return _execute(svc.users().getProfile(userId="me"))["historyId"]


def trash(svc, msg_id: str) -> None:
    _execute(svc.users().messages().trash(userId="me", id=msg_id))


def mark_spam(svc, msg_id: str) -> None:
    _execute(svc.users().messages().modify(userId="me", id=msg_id,
                                           body={"addLabelIds": ["SPAM"], "removeLabelIds": ["INBOX"]}))


def relabel(svc, msg_id: str, add: list[str], remove: list[str] = ()) -> None:
    _execute(svc.users().messages().modify(userId="me", id=msg_id,
                                           body={"addLabelIds": add, "removeLabelIds": list(remove)}))


def label_id(svc, name: str) -> str:
    return next(l["id"] for l in _execute(svc.users().labels().list(userId="me"))["labels"] if l["name"] == name)


def ensure_label(svc, name: str) -> str:
    """The label's id, creating the label if it doesn't exist yet."""
    try:
        return label_id(svc, name)
    except StopIteration:
        return _execute(svc.users().labels().create(userId="me", body={"name": name}))["id"]


def block(svc, msg_id: str, blocked_label: str) -> None:
    """Into the Blocked label and out of the inbox (the nightly purge trashes it after a week)."""
    _execute(svc.users().messages().modify(userId="me", id=msg_id,
                                           body={"addLabelIds": [blocked_label], "removeLabelIds": ["INBOX"]}))


def changes_since(svc, start: str) -> tuple[list[dict], list[dict], str]:
    """(messages added, label additions, new checkpoint) since `start`; each item is {id, labels}.
    Raises HttpError 404 if `start` is too old."""
    added: list[dict] = []
    labelled: list[dict] = []
    tok, latest = None, start
    while True:
        res = _execute(svc.users().history().list(userId="me", startHistoryId=start, pageToken=tok,
                                                  historyTypes=["messageAdded", "labelAdded"]))
        for h in res.get("history", []):
            for m in h.get("messagesAdded", []):
                added.append({"id": m["message"]["id"], "labels": m["message"].get("labelIds", [])})
            for m in h.get("labelsAdded", []):
                labelled.append({"id": m["message"]["id"], "labels": m.get("labelIds", [])})
        latest = res.get("historyId", latest)
        tok = res.get("nextPageToken")
        if not tok:
            return added, labelled, latest


def search(svc, q: str, include_trash: bool = False) -> list[str]:
    ids, tok = [], None
    while True:
        res = _execute(svc.users().messages().list(userId="me", q=q, maxResults=500, pageToken=tok,
                                                   includeSpamTrash=include_trash))
        ids += [m["id"] for m in res.get("messages", [])]
        tok = res.get("nextPageToken")
        if not tok:
            return ids


def user_label_ids(svc) -> list[str]:
    return [l["id"] for l in _execute(svc.users().labels().list(userId="me"))["labels"] if l["type"] == "user"]


def move_to_blocked(svc, ids: list[str], blocked_label: str) -> None:
    """Into Blocked; out of the inbox and every other user label."""
    others = [l for l in user_label_ids(svc) if l != blocked_label]
    for i in range(0, len(ids), 1000):
        _execute(svc.users().messages().batchModify(userId="me", body={
            "ids": ids[i:i + 1000], "addLabelIds": [blocked_label], "removeLabelIds": ["INBOX", *others]}))


def trash_many(svc, ids: list[str]) -> None:
    """Trash in batches of 1000 (one API call each, instead of one per message)."""
    for i in range(0, len(ids), 1000):
        _execute(svc.users().messages().batchModify(userId="me", body={"ids": ids[i:i + 1000],
                                                                        "addLabelIds": ["TRASH"]}))
