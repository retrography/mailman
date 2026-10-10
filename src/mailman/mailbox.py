"""A mailbox: what the engine needs from a mail account, whoever provides it.

Accounts are named after their provider: `gmail` (the Gmail API; the first one, and the default) and `outlook`
(Microsoft Graph, see mailman.outlook). An account exists once it has a sign-in. The engine speaks Gmail's
vocabulary everywhere — label names, the system labels INBOX / SPAM / TRASH / UNREAD / STARRED, Gmail's search
words, and messages in the Gmail API's shape — and a provider translates.

A mailbox offers:
  address()                        the address that signed in
  get(id) · headers(id)            a message in the Gmail API's shape · its From and Subject
  search(q) · count(q) · in_label(label) · labels()
  apply(ids, trash, add, remove)   the operations of an outcome (label names; `user_labels`: every label of yours)
  label_id(name)                   how a label appears in a message's labels and in changes()
  checkpoint() · changes(start)    what arrived, and what was labelled, since a checkpoint (Expired: too old)
  waiter(renew)                    blocks until mail may have arrived
"""

from __future__ import annotations

import json
import os
import time

from googleapiclient.errors import HttpError
from imapclient import IMAPClient
from imapclient.exceptions import IMAPClientError

from mailman import gmail, outlook

ACCOUNTS = ("gmail", "outlook")
TITLES = {"gmail": "Gmail", "outlook": "Outlook"}
SYSTEM = {"INBOX", "SPAM", "TRASH", "UNREAD", "STARRED", "IMPORTANT"}
ERRORS = (HttpError, outlook.GraphError)   # a refusal by the provider; both carry `status_code`


class Expired(Exception):
    """The checkpoint is too old for the provider to say what changed since."""


def key(name: str, account: str) -> str:
    """The name a per-mailbox value is kept under (state, caches): plain for Gmail, the first mailbox."""
    return name if account == "gmail" else f"{name}:{account}"


def signed_in(account: str) -> float | None:
    """When the account's sign-in was made (it changes when you sign in again); None without one."""
    if account == "gmail":
        path = os.environ.get("MAILMAN_TOKEN_FILE", "")
        if path and os.path.exists(path):
            return os.stat(path).st_mtime
        return 1.0 if os.environ.get("GMAIL_REFRESH_TOKEN") else None
    try:
        return float(json.loads(outlook.token_file().read_text()).get("signed_in") or 1.0)
    except (OSError, ValueError):
        return None


def connected() -> list[str]:
    return [a for a in ACCOUNTS if signed_in(a) is not None]


def connect(account: str = "gmail", settings: dict | None = None):
    if account == "gmail":
        return GmailBox()
    if account == "outlook":
        return outlook.OutlookBox(settings)
    raise ValueError(f"no such mailbox: {account} (there are {', '.join(ACCOUNTS)})")


class GmailBox:
    name, title = "gmail", "Gmail"

    def __init__(self, svc=None):
        self.svc = svc or gmail.service()
        self.ids: dict[str, str] = {}   # label ids by name (cached; created when missing)

    def address(self) -> str:
        return gmail._execute(self.svc.users().getProfile(userId="me"))["emailAddress"].lower()

    def get(self, msg_id: str) -> dict:
        return gmail.get(self.svc, msg_id)

    def headers(self, msg_id: str) -> dict[str, str]:
        meta = gmail._execute(self.svc.users().messages().get(userId="me", id=msg_id, format="metadata",
                                                              metadataHeaders=["From", "Subject"]))
        h = {x["name"]: x["value"] for x in meta["payload"].get("headers", [])}
        return {"from": h.get("From", ""), "subject": h.get("Subject", "")}

    def search(self, q: str, include_trash: bool = False, limit: int | None = None) -> list[str]:
        """Ids of the messages a search finds, newest first (`limit`: only the first so many)."""
        if limit and limit <= 500:
            res = gmail._execute(self.svc.users().messages().list(userId="me", q=q, maxResults=limit,
                                                                  includeSpamTrash=include_trash))
            return [m["id"] for m in res.get("messages", [])]
        return gmail.search(self.svc, q, include_trash)[:limit or None]

    def count(self, q: str, limit: int, exclude: str | None = None) -> int:
        return sum(1 for i in self.search(q, limit=limit) if i != exclude)

    def in_label(self, label: str) -> list[str]:
        """Every message carrying the label, Trash and Spam included."""
        M, lid = self.svc.users().messages(), self.label_id(label)
        ids, tok = [], None
        while True:
            res = gmail._execute(M.list(userId="me", labelIds=[lid], maxResults=500, includeSpamTrash=True,
                                        pageToken=tok))
            ids += [m["id"] for m in res.get("messages", [])]
            tok = res.get("nextPageToken")
            if not tok:
                return ids

    def labels(self) -> list[str]:
        return sorted(l["name"] for l in gmail._execute(self.svc.users().labels().list(userId="me"))["labels"]
                      if l["type"] == "user")

    def label_id(self, name: str) -> str:
        if name in SYSTEM:
            return name
        if name not in self.ids:
            self.ids[name] = gmail.ensure_label(self.svc, name)
        return self.ids[name]

    def apply(self, ids: list[str], trash: bool, add: list[str], remove: list[str], batch: bool = False) -> None:
        """Label changes of one outcome go to Gmail as a single call (`batch`: one call per 1000 messages)."""
        add_ids = [self.label_id(n) for n in add]
        remove_ids = [i for n in remove
                      for i in ([l for l in gmail.user_label_ids(self.svc) if l not in add_ids] if n == "user_labels"
                                else [self.label_id(n)])]
        if len(ids) == 1 and not batch:
            if trash:
                gmail.trash(self.svc, ids[0])
            if add or remove:
                gmail.relabel(self.svc, ids[0], add_ids, remove_ids)
            return
        if trash:
            add_ids = [*add_ids, "TRASH"]
        change = {k: v for k, v in (("addLabelIds", add_ids), ("removeLabelIds", remove_ids)) if v}
        for k in range(0, len(ids), 1000):
            gmail._execute(self.svc.users().messages().batchModify(userId="me", body={"ids": ids[k:k + 1000], **change}))

    def checkpoint(self) -> str:
        return gmail.history_id(self.svc)

    def changes(self, start: str, watch: list[str] = ()) -> tuple[list[dict], list[dict], str]:
        """(messages added, label additions, new checkpoint) since `start`; each item is {id, labels}."""
        try:
            return gmail.changes_since(self.svc, start)
        except HttpError as e:
            if e.status_code == 404:
                raise Expired(start) from e
            raise

    def waiter(self, renew: int) -> "Idle":
        return Idle(self.address(), renew)


class Idle:
    """An IMAP IDLE connection on the Gmail inbox: the wait ends as soon as mail arrives."""

    def __init__(self, address: str, renew: int):
        creds = gmail.credentials()
        self.server = IMAPClient("imap.gmail.com", ssl=True, timeout=renew + 60)
        self.server.oauth2_login(address, creds.token)   # the account that signed in, whatever the profile says
        self.server.select_folder("INBOX", readonly=True)

    def arm(self) -> bool:
        """Enter IDLE. Mail can arrive between two commands ('* 86 EXISTS'); imapclient then mistakes that
        notice for the reply to IDLE. Consume it with a NOOP and try again. Returns True if mail arrived."""
        for attempt in range(3):
            try:
                self.server.idle()
                return False
            except IMAPClientError as e:
                if "unexpected response" not in str(e) or attempt == 2:
                    raise
                self.server.noop()
        return True

    def wait(self, seconds: float, stopping) -> None:
        deadline, events = time.time() + seconds, []
        while time.time() < deadline and not events and not stopping():
            events = self.server.idle_check(timeout=min(5, max(1, int(deadline - time.time()))))
        self.server.idle_done()

    def alive(self) -> bool:
        try:   # long processing can outlive the IMAP connection
            self.server.noop()
            return True
        except Exception:
            return False

    def close(self) -> None:
        try:
            self.server.logout()
        except Exception:
            pass
