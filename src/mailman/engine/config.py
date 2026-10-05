"""Load the configuration directory and resolve profile placeholders.

A string that is exactly one placeholder, "{{identity.emails}}", becomes the value itself (a list stays a
list; inside a YAML list it is spliced in). A placeholder inside longer text is substituted as text.
Paths walk the profile; filters after `|` transform the value:
  full_names  people → "First Middle Last"         emails     every `emails` entry found underneath
  mailboxes   addresses → canonical mailboxes      domains    addresses → their domains
  first       first item                           lower      lower-case
"""

from __future__ import annotations

import os
import re
from pathlib import Path

import yaml

from mailman.engine import addr

ROOT = Path(__file__).resolve().parents[3]
FILES = ("profile", "settings", "lists", "facts", "jev", "rules", "outcomes", "jobs")
PLACEHOLDER = re.compile(r"\{\{\s*(.+?)\s*\}\}")


def config_dir() -> Path:
    return Path(os.environ.get("MAILMAN_CONFIG", ROOT / "config"))


def full_name(p: dict) -> str:
    return " ".join([p["first_name"], *p.get("middle_names", []), *p.get("last_names", [])])


def _emails(v) -> list[str]:
    if isinstance(v, dict):
        return [e for k, x in v.items() for e in (x if k == "emails" else _emails(x))]
    if isinstance(v, list):
        return [e for x in v for e in _emails(x)]
    return []


class Config:
    def __init__(self, directory: Path | None = None):
        self.dir = directory or config_dir()
        self.load()

    def mtime(self) -> float:
        return max((self.dir / f"{n}.yaml").stat().st_mtime for n in FILES)

    def load(self) -> None:
        """Read all files; nothing changes unless every file loads (a bad file keeps the old configuration)."""
        mtime = self.mtime()
        raw = {n: yaml.safe_load((self.dir / f"{n}.yaml").read_text()) or {} for n in FILES}
        new = Config.__new__(Config)
        new.dir, new.profile = self.dir, raw["profile"]
        new.settings = {}
        new.filters = {
            "full_names": lambda v: [full_name(p) for p in v],
            "emails": _emails,
            "mailboxes": lambda v: [addr.canonical(a, new.settings) for a in v],
            "domains": lambda v: sorted({addr.domain_of(a) for a in v}),
            "first": lambda v: v[0],
            "lower": lambda v: [x.lower() for x in v] if isinstance(v, list) else v.lower(),
        }
        new.settings.update(new.resolve(raw["settings"]))
        from mailman.engine.facts import ENGINE
        if new.settings.get("min_engine", 0) > ENGINE:
            raise RuntimeError(f"this configuration needs engine {new.settings['min_engine']}; this is engine "
                               f"{ENGINE} — restart the daemon on the current code")
        for n in ("lists", "facts", "jev", "rules", "outcomes", "jobs"):
            setattr(new, n, new.resolve(raw[n]))
        self.__dict__.update(new.__dict__)
        self.loaded_at = mtime

    # ------------------------------------------------------------ placeholders

    def value(self, expr: str):
        path, *filters = [p.strip() for p in expr.split("|")]
        v = self.profile
        for part in re.findall(r"[^.\[\]]+", path):
            v = v[int(part)] if isinstance(v, list) else v[part]
        for f in filters:
            v = self.filters[f](v)
        return v

    def resolve(self, node):
        if isinstance(node, dict):
            return {k: self.resolve(v) for k, v in node.items()}
        if isinstance(node, list):
            out = []
            for item in node:
                r = self.resolve(item)
                if isinstance(item, str) and PLACEHOLDER.fullmatch(item.strip()) and isinstance(r, list):
                    out.extend(r)
                else:
                    out.append(r)
            return out
        if isinstance(node, str) and "{{" in node:
            m = PLACEHOLDER.fullmatch(node.strip())
            if m:
                return self.value(m.group(1))
            return PLACEHOLDER.sub(lambda m: str(self.value(m.group(1))), node)
        return node
