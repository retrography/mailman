"""Address helpers. What counts as the same mailbox is set in settings.yaml → addresses."""

from __future__ import annotations

DEFAULTS = {"alias_domains": {}, "ignore_dots": [], "ignore_plus_tag": True,
            "second_level_labels": ["co", "com", "org", "net", "gov", "ac", "edu"]}


def _opts(settings: dict | None) -> dict:
    return {**DEFAULTS, **((settings or {}).get("addresses") or {})}


def domain_of(address: str) -> str:
    return address.rsplit("@", 1)[-1].lower().strip(">") if "@" in address else ""


def canonical(address: str, settings: dict | None = None) -> str:
    """Mailbox identity: lower-case, alias domains merged, '+tag' dropped, dots ignored where set."""
    o = _opts(settings)
    address = address.strip().lower()
    if "@" not in address:
        return address
    local, dom = address.rsplit("@", 1)
    dom = o["alias_domains"].get(dom, dom)
    if o["ignore_plus_tag"]:
        local = local.split("+", 1)[0]
    if dom in o["ignore_dots"]:
        local = local.replace(".", "")
    return f"{local}@{dom}"


def registrable(domain: str, settings: dict | None = None) -> str:
    """email.ns.nl → ns.nl; mail.shop.co.uk → shop.co.uk."""
    parts = domain.lower().split(".")
    if len(parts) >= 3 and parts[-2] in _opts(settings)["second_level_labels"] and len(parts[-1]) == 2:
        return ".".join(parts[-3:])
    return ".".join(parts[-2:])


def covers(domain: str, parent: str) -> bool:
    """`domain` is `parent` or one of its subdomains."""
    return domain == parent or domain.endswith("." + parent)
