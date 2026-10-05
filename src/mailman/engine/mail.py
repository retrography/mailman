"""Email fields: the raw material detectors and Jev work on.

A field is named by a short path, optionally with a modifier:
  id · thread · labels · snippet · subject · body · links
  sender.name · sender.address · sender.domain · sender.registrable · sender.domain_label · sender.mailbox
  reply_to.address · reply_to.domain
  to · cc                 header address pairs (name, address) — for the `addresses` detector
  to.addresses · cc.addresses · recipients · to.names
  header.<name>
  …​.head(n) · .tail(n) · .stripped
"""

from __future__ import annotations

import re
from email.utils import getaddresses, parseaddr

from mailman.engine import addr
from mailman.gmail import Message

MODIFIER = re.compile(r"\.(head|tail)\((\d+)\)$|\.(stripped)$")


def clean(text: str, steps: list[dict], limit: int) -> str:
    for s in steps:
        text = re.sub(s["replace"], s["with"], text)
    return text.strip()[:limit]


class Email:
    def __init__(self, msg: Message, settings: dict):
        self.msg, self.settings = msg, settings
        body = settings["email"]["body"]
        self.body = clean(msg.body_text, body["cleaning"], body["max_chars"])
        self.sender_name, sender = parseaddr(msg.h("from"))
        self.sender = sender.lower()
        self.cache: dict[str, object] = {}

    def pairs(self, header: str) -> list[tuple[str, str]]:
        return getaddresses([self.msg.h(header)])

    def field(self, spec: str):
        if spec not in self.cache:
            self.cache[spec] = self._field(spec)
        return self.cache[spec]

    def _field(self, spec: str):
        m = MODIFIER.search(spec)
        if m:
            v = self.field(spec[:m.start()])
            if m.group(3):
                return v.strip()
            n = int(m.group(2))
            return v[:n] if m.group(1) == "head" else v[-n:]
        msg, s = self.msg, self.settings
        domain = addr.domain_of(self.sender)
        simple = {
            "id": lambda: msg.id, "thread": lambda: msg.thread_id, "labels": lambda: msg.label_ids,
            "snippet": lambda: msg.snippet, "subject": lambda: msg.h("subject"), "body": lambda: self.body,
            "links": lambda: msg.links,
            "sender.name": lambda: self.sender_name, "sender.address": lambda: self.sender,
            "sender.domain": lambda: domain, "sender.registrable": lambda: addr.registrable(domain, s),
            "sender.domain_label": lambda: addr.registrable(domain, s).split(".")[0],
            "sender.mailbox": lambda: addr.canonical(self.sender, s),
            "reply_to.address": lambda: parseaddr(msg.h("reply-to"))[1].lower(),
            "reply_to.domain": lambda: addr.domain_of(parseaddr(msg.h("reply-to"))[1]),
            "to.addresses": lambda: [a.lower() for _, a in self.pairs("to")],
            "cc.addresses": lambda: [a.lower() for _, a in self.pairs("cc")],
            "recipients": lambda: [a.lower() for h in ("to", "cc") for _, a in self.pairs(h)],
            "to.names": lambda: " ".join(n for n, _ in self.pairs("to")),
        }
        if spec in simple:
            return simple[spec]()
        if spec.startswith("header."):
            return msg.h(spec[7:])
        raise KeyError(spec)

    def has_field(self, spec: str) -> bool:
        try:
            self.field(spec)
            return True
        except KeyError:
            return False
