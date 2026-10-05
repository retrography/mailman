"""Facts: observations about one email, each defined in config/facts.yaml as a detector with parameters.

Facts are computed only when asked for and then remembered. A name resolves, in this order, to: a fact
from facts.yaml, a Jev answer (`theme`, `theme.conf`, `expects.p.nothing`, `written_personally.p`), an engine
state fact (`labelled`, `label_kids`), an email field (`sender.domain`), or a part of another fact
(`platform_record.languages`, `phone_countries.0`).

Conditions (used by rules, derived facts, and gates):
  fact: value            equals              fact: [a, b]           is one of
  fact: {exists: true}   has a value         fact: {not: [a, b]}    has a value other than these
  fact: {min: 2}         at least            fact: {max: 2}         at most
  fact: {count: 1}       a list of this size fact: {in: $other}     is in another fact's list
  all: […]   any: […]   not: {…}            sender_in: [lists]     the sender is on one of these lists
A fact without a value never matches (except `exists: false`). `$name` refers to another fact's value.
`engine` is this engine's version: a rule that uses something newer starts with `engine: {min: N}`, so a
daemon still running older code skips it instead of misreading it.
"""

from __future__ import annotations

import re

import phonenumbers
import pycountry

from mailman.engine import addr, scripts
from mailman.engine.mail import Email

COUNTRY_CODES = {c.alpha_2: c.name for c in pycountry.countries}
FLAGS = {"ignore_case": re.I, "multiline": re.M, "dotall": re.S}


def flags(spec: dict) -> int:
    f = 0
    for name in spec.get("flags", []):
        f |= FLAGS[name]
    return f


ENGINE = 3   # raised when the engine learns something a configuration can depend on (fact `engine`)


class Facts:
    def __init__(self, engine, email: Email, answers: dict | None = None, lookups=None):
        self.engine, self.email, self.answers, self.lookups = engine, email, answers or {}, lookups
        self.ask_jev = False   # True: reading an answer Jev was not asked for yet raises NeedJev
        self.values: dict[str, object] = {}
        self.state: dict[str, object] = {}

    # ------------------------------------------------------------ resolution

    def get(self, name: str):
        if name not in self.values:
            self.values[name] = self._resolve(name)   # (NeedJev passes through; nothing is remembered)
        return self.values[name]

    def ref(self, v):
        """`$name` → that fact's value; anything else as it is."""
        return self.get(v[1:]) if isinstance(v, str) and v.startswith("$") else v

    def _resolve(self, name: str):
        defs = self.engine.config.facts
        if name in defs:
            return self.run(defs[name], name)
        if name in self.state:
            return self.state[name]
        if name == "engine":   # a rule that needs a newer engine starts with `engine: {min: N}`
            return ENGINE
        jev = self._answer(name)
        if jev is not _MISSING:
            return jev
        if self.email.has_field(name):
            return self.email.field(name)
        if "." in name:   # a part of another fact: record field or list index
            parent, key = name.rsplit(".", 1)
            v = self.get(parent)
            if isinstance(v, dict):
                return v.get(key)
            if isinstance(v, list) and key.isdigit():
                return v[int(key)] if int(key) < len(v) else None
        return None

    def _answer(self, name: str):
        base, _, rest = name.partition(".")
        if base not in self.engine.jev.questions:
            return _MISSING
        if base not in self.answers and self.ask_jev:
            raise NeedJev(base)
        a = self.answers.get(base)
        if a is None:
            return None
        if isinstance(a, dict):   # choice
            if not rest:
                return a["choice"]
            if rest in ("conf", "raw"):
                return a.get(rest)
            if rest.startswith("p."):
                return a["top"].get(rest[2:])
            return None
        if not rest:              # yes/no
            return a >= self.engine.jev.yes_threshold(base)
        return a if rest == "p" else None

    def logged(self) -> dict:
        """The facts computed for this email that are worth logging (not the raw fields)."""
        return {**{k: v for k, v in self.values.items() if not self.email.has_field(k)}, **self.state}

    # ------------------------------------------------------------ conditions

    def matches(self, cond: dict) -> bool:
        for key, want in cond.items():
            if key == "all":
                ok = all(self.matches(c) for c in want)
            elif key == "any":
                ok = any(self.matches(c) for c in want)
            elif key == "not":
                ok = not self.matches(want)
            elif key == "sender_in":
                names = want if isinstance(want, list) else [want]
                ok = any(self.engine.lists.contains(n, self.email.sender) for n in names)
            else:
                ok = self.leaf(self.get(key), want)
            if not ok:
                return False
        return True

    def leaf(self, have, want) -> bool:
        if isinstance(want, dict):
            if "exists" in want and (have is not None) != want["exists"]:
                return False
            if have is None:
                return want.get("exists") is False
            for op, x in want.items():
                x = self.ref(x)
                if op == "not" and have in (x if isinstance(x, list) else [x]):
                    return False
                if op == "min" and not have >= x:
                    return False
                if op == "max" and not have <= x:
                    return False
                if op == "count" and len(have) != x:
                    return False
                if op == "in" and have not in (x or []):
                    return False
            return True
        if have is None:
            return False
        want = self.ref(want)
        return have in want if isinstance(want, list) else have == want

    # ------------------------------------------------------------ detectors

    def run(self, spec, name: str = ""):
        if isinstance(spec, str):   # a reference to another fact or field
            return self.get(spec)
        for kind, fn in DETECTORS.items():
            if kind in spec:
                return fn(self, spec[kind], spec, name)
        raise ValueError(f"fact {name!r}: no known detector in {list(spec)}")

    def text(self, source) -> str:
        """One field, or several joined by a line break."""
        return "\n".join(self.get(s) or "" for s in source) if isinstance(source, list) else (self.get(source) or "")


_MISSING = object()


class NeedJev(Exception):
    """A rule read the answer to a question (args[0]) that Jev has not been asked yet."""


def d_pattern(f: Facts, pattern: str, spec: dict, name: str):
    rx = re.compile(pattern, flags(spec))
    returns = spec.get("returns", "bool")
    sources = spec["in"] if isinstance(spec["in"], list) else [spec["in"]]
    if returns == "all":
        return [m for s in sources for m in rx.findall(f.text(s))]
    for s in sources:
        m = rx.search(f.text(s))
        if m:
            return True if returns == "bool" else m.group(m.lastindex or 0)
    return False if returns == "bool" else None


def d_header(f: Facts, header: str, spec: dict, name: str):
    v = f.email.msg.h(header)
    return v.lower() in [x.lower() for x in spec["is"]] if "is" in spec else bool(v)


def d_any(f: Facts, items: list, spec: dict, name: str):
    return any(bool(f.run(i)) for i in items)


def d_all(f: Facts, items: list, spec: dict, name: str):
    return all(bool(f.run(i)) for i in items)


def d_field(f: Facts, source: str, spec: dict, name: str):
    return f.get(source)


def d_value(f: Facts, v, spec: dict, name: str):
    return v


def d_derived(f: Facts, cases: list, spec: dict, name: str):
    for case in cases:
        if "when" not in case or f.matches(case["when"]):
            return f.ref(case.get("value", True))
    return None


def d_first_available(f: Facts, items: list, spec: dict, name: str):
    for i in items:
        v = f.run(i)
        if v is not None:
            return v
    return None


def d_language(f: Facts, source: str, spec: dict, name: str):
    """The language of a field, from its writing system alone (the body's language is asked of the classifier)."""
    return scripts.language(f.text(source))


def d_script(f: Facts, source: str, spec: dict, name: str):
    return scripts.script(f.text(source))


def d_country(f: Facts, p: dict, spec: dict, name: str):
    if "domain" in p:   # country of a country-code ending; generic and vanity endings say nothing
        tld = (f.get(p["domain"]) or "").rsplit(".", 1)[-1]
        if len(tld) == 2 and tld not in p.get("vanity", []):
            code = p.get("aliases", {}).get(tld, tld.upper())
            return code if code in COUNTRY_CODES else None
        return None
    text = f.text(p["phones"])[:p.get("max_chars", 20000)]   # countries of international phone numbers
    regions = {phonenumbers.region_code_for_number(m.number) for m in phonenumbers.PhoneNumberMatcher(text, None)}
    regions.discard(None)
    return sorted(regions)


def d_list(f: Facts, p: dict, spec: dict, name: str):
    value = f.get(p["value"]) or ""
    lists = f.engine.lists
    names = p["on"] if isinstance(p["on"], list) else [p["on"]]
    returns = p.get("returns", "bool")
    if returns == "bool":
        return any(lists.contains(n, value, p.get("where")) for n in names)
    for n in names:
        rec = lists.record_for(n, value.rsplit("@", 1)[-1], p.get("where"))
        if rec:
            return rec
    return None


def d_lookup(f: Facts, p: dict, spec: dict, name: str):
    if f.lookups is None:
        return None   # no mailbox to look in: the fact has no value
    query = re.sub(r"\{([\w.]+)\}", lambda m: str(f.get(m.group(1)) or ""), p["search"])
    return f.lookups.count(name, query, p.get("max", 5), f.email.msg.id if p.get("exclude_self") else None,
                           p.get("cache", []))


def d_addresses(f: Facts, p: dict, spec: dict, name: str):
    """Addresses in header fields (`to`, `cc`: name + address) or found in text, filtered by `where`."""
    s = f.engine.config.settings
    if p.get("from_text"):
        rx = re.compile(p.get("pattern", s["addresses"]["pattern"]))
        pairs = [("", a) for a in rx.findall(f.text(p["in"]))]
    else:
        pairs = [pa for h in p["in"] for pa in f.email.pairs(h)]
    w = dict(p.get("where", {}))
    if isinstance(w.get("domain_in"), dict):   # the domains of a records list
        w["domain_in"] = f.engine.lists.domains(w["domain_in"]["list"], w["domain_in"].get("where"))
    sender = addr.canonical(f.email.sender, s)
    spellings: dict[str, set[str]] = {}   # your mailboxes → the ways you write them (before any +tag)
    for mine in w.get("respelling_of", []):
        spellings.setdefault(addr.canonical(mine, s), set()).add(mine.lower().split("@")[0].split("+")[0])
    out = []
    for n, a in pairs:
        if not a:
            continue
        low, box = a.lower(), addr.canonical(a, s)
        if "address_is" in w and low != w["address_is"].lower():
            continue
        if "mailbox_in" in w and box not in w["mailbox_in"]:
            continue
        if "mailbox_not_in" in w and box in w["mailbox_not_in"]:
            continue
        if "domain_in" in w and box.split("@")[1] not in w["domain_in"]:
            continue
        if w.get("has_plus_tag") and "+" not in low.split("@")[0]:
            continue
        if "tag_not_in" in w and low.split("@")[0].partition("+")[2] in [t.lower() for t in w["tag_not_in"]]:
            continue   # a +tag you do use
        if "respelling_of" in w and (box not in spellings or low.split("@")[0].split("+")[0] in spellings[box]):
            continue   # not one of your mailboxes, or written the way you write it
        if w.get("not_the_sender") and box == sender:
            continue
        if w.get("name_present") and not n.strip():
            continue
        if "name_without" in w and w["name_without"] in n:
            continue
        out.append({"address": low, "mailbox": box, "name": n.strip()}[p.get("output", "address")])
    if p.get("unique"):
        out = sorted(set(out))
    return {"list": out, "bool": bool(out), "count": len(out), "first": out[0] if out else ""}[p.get("as", "list")]


def d_extract(f: Facts, p: dict, spec: dict, name: str):
    """Candidate strings: collected from fields and patterns, split, cleaned, filtered, de-duplicated."""
    raw: list[str] = []
    for src in p["sources"]:
        if "pattern" in src:
            raw += re.compile(src["pattern"], flags(src)).findall(f.text(src["in"]))
        else:
            v = f.get(src["field"]) or ""
            if v.strip() and not (src.get("unless_contains") and src["unless_contains"] in v):
                raw.append(v)
    split = re.compile(p["split"], flags({"flags": p.get("split_flags", [])})) if "split" in p else None
    keep = p.get("keep", {})
    out: list[str] = []
    for r in raw:
        for part in (split.split(r) if split else [r]):
            for step in p.get("clean", []):
                part = part.strip(step["strip"]) if "strip" in step else re.sub(step["replace"], step["with"], part)
            if keep.get("min_chars", 0) <= len(part) <= keep.get("max_chars", 10 ** 6) and \
                    ("matching" not in keep or re.search(keep["matching"], part)):
                out.append(part)
    out += [v for v in (f.get(a) for a in p.get("append", [])) if v]
    seen: set[str] = set()
    unique = []
    for o in out:
        k = o.lower().replace(" ", "")
        if k not in seen:
            seen.add(k)
            unique.append(o)
    return unique[:p.get("limit", len(unique))]


DETECTORS = {"pattern": d_pattern, "header": d_header, "any": d_any, "all": d_all, "field": d_field,
             "derived": d_derived, "first_available": d_first_available, "language": d_language,
             "script": d_script, "country": d_country, "list": d_list, "lookup": d_lookup,
             "addresses": d_addresses, "extract": d_extract, "value": d_value}
