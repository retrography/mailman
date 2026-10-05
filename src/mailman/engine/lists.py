"""Lists (config/lists.yaml): any number of them, each `holds` senders, patterns, or records.

  senders   an entry is an address (exact; on webmail domains compared as delivered) or a domain (covers its
            subdomains)
  patterns  wildcard entries matched against the lower-cased value
  records   entries with fields; matched on the field named by `match` (a list of domains)

An entry is a plain string or {value: …, note: …}; a record is a mapping with a `name`.
"""

from __future__ import annotations

from collections import defaultdict
from fnmatch import fnmatch
from pathlib import Path

import yaml

from mailman.engine import addr


class Lists:
    def __init__(self, raw: dict, settings: dict):
        self.raw, self.settings = raw, settings
        ref = (settings.get("addresses") or {}).get("webmail")   # which domains are shared mailboxes
        self.webmail = set(self.domains(ref["list"], ref.get("where"))) if ref else set()

    def names(self) -> list[str]:
        return list(self.raw)

    def kind(self, name: str) -> str:
        return self.raw[name].get("holds", "senders")

    def values(self, name: str) -> list[str]:
        return [(e["value"] if isinstance(e, dict) else e) for e in self.raw[name].get("entries", [])]

    def records(self, name: str, where: dict | None = None) -> list[dict]:
        return [r for r in self.raw[name].get("entries", [])
                if all(r.get(k) in (v if isinstance(v, list) else [v]) for k, v in (where or {}).items())]

    def domains(self, name: str, where: dict | None = None) -> list[str]:
        field = self.raw[name].get("match", "domains")
        return [d.lower() for r in self.records(name, where) for d in r.get(field, [])]

    # ------------------------------------------------------------ matching

    def has_sender(self, name: str, address: str) -> bool:
        """An address (webmail compared as delivered; others exactly) or its domain / a parent domain is listed."""
        address = address.lower()
        entries = [v.lower() for v in self.values(name)]
        domain = address.rsplit("@", 1)[-1]
        if domain in self.webmail:
            return addr.canonical(address, self.settings) in {addr.canonical(e, self.settings)
                                                              for e in entries if "@" in e}
        return any(address == e if "@" in e else addr.covers(domain, e) for e in entries)

    def has_pattern(self, name: str, value: str) -> bool:
        return any(fnmatch(value.lower(), p.lower()) for p in self.values(name))

    def record_for(self, name: str, domain: str, where: dict | None = None) -> dict | None:
        field = self.raw[name].get("match", "domains")
        domain = domain.lower()
        return next((r for r in self.records(name, where)
                     if any(addr.covers(domain, d.lower()) for d in r.get(field, []))), None)

    def contains(self, name: str, value: str, where: dict | None = None):
        kind = self.kind(name)
        if kind == "patterns":
            return self.has_pattern(name, value)
        if kind == "records":
            return self.record_for(name, value.rsplit("@", 1)[-1], where) is not None
        return self.has_sender(name, value)

    # ------------------------------------------------------------ maintenance

    def protected_domains(self, spec: dict) -> set[str]:
        """Domains never collapsed into one entry: `lists` (record domains, or the domains of patterns and
        senders) and `addresses` (their domains)."""
        out: set[str] = set()
        for name in spec.get("lists", []):
            if self.kind(name) == "records":
                out.update(self.domains(name))
            else:
                out.update(v.split("@")[-1].lstrip("*.").lower() for v in self.values(name))
        out.update(a.split("@")[1].lower() for a in spec.get("addresses", []))
        return out

    def normalise(self, name: str, entries: list[str]) -> tuple[list[str], dict]:
        """Dedupe; collapse ≥ N addresses of one unprotected domain into the domain; drop addresses that a
        listed domain already covers. Returns (domains first, then addresses; a report)."""
        spec = self.raw[name].get("normalise", {})
        at = spec.get("collapse_to_domain_at", 0)
        protected = self.protected_domains(spec.get("never_collapse", {}))
        clean = {e.strip().lower() for e in entries if e and e.strip()}
        domains = {e for e in clean if "@" not in e}
        addresses = {e for e in clean if "@" in e}
        by_domain: dict[str, set[str]] = defaultdict(set)
        for a in addresses:
            by_domain[a.split("@", 1)[1]].add(a)

        def covered(d: str, parents: set[str]) -> bool:
            return any(addr.covers(d, p) for p in parents)

        collapsed = {d for d, addrs in by_domain.items()
                     if at and len(addrs) >= at and not covered(d, protected) and not covered(d, domains)}
        domains |= collapsed
        kept = {a for a in addresses if not covered(a.split("@", 1)[1], domains)}
        report = {"collapsed_into_domain": sorted(collapsed), "addresses_dropped": len(addresses) - len(kept),
                  "protected_kept_as_addresses": sorted(d for d, a in by_domain.items()
                                                        if at and len(a) >= at and covered(d, protected))}
        return sorted(domains) + sorted(kept), report

    def set_values(self, name: str, values: list[str]) -> None:
        """Replace a list's entries, keeping the notes of entries that stay."""
        notes = {(e["value"] if isinstance(e, dict) else e).lower(): e for e in self.raw[name].get("entries", [])}
        self.raw[name]["entries"] = [notes.get(v.lower(), v) for v in values]


def save(raw: dict, path: Path) -> None:
    """Write lists.yaml: one entry per line, records and noted entries in flow style."""
    out = ["# Lists (docs/app-design.md §6). Written by the app — notes are fields, comments are not kept.\n"]
    for name, body in raw.items():
        head = {k: v for k, v in body.items() if k != "entries"}
        out.append(f"\n{name}:\n")
        for line in yaml.safe_dump(head, sort_keys=False, allow_unicode=True, width=110).splitlines():
            out.append(f"  {line}\n")
        out.append("  entries:\n")
        for e in body.get("entries", []):
            text = yaml.safe_dump(e, default_flow_style=True, allow_unicode=True, width=10000).strip()
            out.append(f"    - {text.removesuffix('...').strip()}\n")
    tmp = path.with_suffix(".tmp")
    tmp.write_text("".join(out))
    tmp.replace(path)
