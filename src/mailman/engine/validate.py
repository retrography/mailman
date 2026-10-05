"""Check a loaded configuration for references that lead nowhere, and describe what rules can test.

`problems(engine)` returns human-readable findings (an empty list means the configuration is consistent).
`catalogue(engine)` lists every fact with its source, kind of value, and cost — what a rule editor offers.
"""

from __future__ import annotations

from mailman.engine.facts import DETECTORS
from mailman.engine.mail import Email
from mailman.gmail import Message

STATE = ("labelled", "outcome", "engine")
ANSWER_PARTS = ("conf", "raw", "p")


def _fields() -> Email:
    return Email(Message(id="", thread_id="", label_ids=[], headers={}, body_text="", body_html="",
                         snippet="", internal_date=0), {"email": {"body": {"cleaning": [], "max_chars": 1}}})


def known(engine, name: str, probe: Email) -> bool:
    """Can `name` be resolved: a fact, an answer, a state fact, a field, or a part of one of those?"""
    if name in engine.config.facts or name in STATE or name.startswith("label_"):
        return True
    base, _, rest = name.partition(".")
    if base in engine.jev.questions:
        return not rest or rest.split(".")[0] in ANSWER_PARTS
    if probe.has_field(name):
        return True
    return "." in name and known(engine, name.rsplit(".", 1)[0], probe)


def condition_names(cond: dict):
    """(kind, name) for every fact and list a condition mentions."""
    for key, want in cond.items():
        if key in ("all", "any"):
            for c in want:
                yield from condition_names(c)
        elif key == "not":
            yield from condition_names(want)
        elif key == "sender_in":
            for n in (want if isinstance(want, list) else [want]):
                yield "list", n
        else:
            yield "fact", key
            for v in (want.values() if isinstance(want, dict) else [want]):
                if isinstance(v, str) and v.startswith("$"):
                    yield "fact", v[1:]


def problems(engine) -> list[str]:
    cfg, out, probe = engine.config, [], _fields()
    lists = set(engine.lists.names())

    def check(cond: dict, where: str) -> None:
        for kind, name in condition_names(cond):
            if kind == "list" and name not in lists:
                out.append(f"{where}: unknown list “{name}”")
            if kind == "fact" and not known(engine, name, probe):
                out.append(f"{where}: unknown fact “{name}”")

    stages = [s["name"] for s in cfg.rules.get("stages", [])]
    for st in cfg.rules.get("stages", []):
        if st.get("match") not in ("first", "all"):
            out.append(f"stage {st['name']}: match must be first or all")
        names = set()
        for r in cfg.rules.get(st["name"], []):
            where = f"rule {st['name']} / {r.get('name', '?')}"
            if not r.get("name"):
                out.append(f"{where}: a rule needs a name")
            if r.get("name") in names:
                out.append(f"{where}: the name is used twice in this stage")
            names.add(r.get("name"))
            if "when" not in r:
                out.append(f"{where}: no `when`")
                continue
            check(r["when"], where)
            if "unless" in r:
                check(r["unless"], where)
            if st["match"] == "all":
                if "label" not in r:
                    out.append(f"{where}: a rule in an `all` stage gives a `label`")
            elif r.get("then") not in cfg.outcomes:
                out.append(f"{where}: unknown outcome “{r.get('then')}”")
    for key in cfg.rules:
        if key not in ("stages", "default_outcome") and key not in stages:
            out.append(f"rules: “{key}” is not a stage")

    for name, spec in cfg.facts.items():
        kinds = [k for k in DETECTORS if k in spec]
        if not kinds:
            out.append(f"fact {name}: no known detector ({', '.join(DETECTORS)})")
            continue
        if "derived" in spec:
            for case in spec["derived"]:
                if "when" in case:
                    check(case["when"], f"fact {name}")
        if "list" in spec:
            on = spec["list"]["on"]
            for n in (on if isinstance(on, list) else [on]):
                if n not in lists:
                    out.append(f"fact {name}: unknown list “{n}”")

    for name, q in engine.jev.questions.items():
        if q.get("type") not in ("choice", "yes_no"):
            out.append(f"question {name}: type must be choice or yes_no")
        w = q["ask"].get("when") if isinstance(q.get("ask"), dict) else None
        if w and w["answer"] not in engine.jev.questions:
            out.append(f"question {name}: asked when “{w['answer']}” … but there is no such question")
        src = q.get("options_from")
        if isinstance(src, dict) and src["fact"] not in cfg.facts:
            out.append(f"question {name}: options from unknown fact “{src['fact']}”")

    for name, job in cfg.jobs.get("jobs", {}).items():
        if "outcome" in job and job["outcome"] not in cfg.outcomes:
            out.append(f"job {name}: unknown outcome “{job['outcome']}”")
        if "sync_list" in job and job["sync_list"] not in lists:
            out.append(f"job {name}: unknown list “{job['sync_list']}”")
    for t in cfg.jobs.get("triggers", []):
        if t["run"] not in cfg.jobs.get("jobs", {}):
            out.append(f"trigger {t['on']}: unknown job “{t['run']}”")
        if t["on"] not in ("new_mail", "label_added", "start", "schedule", "interval"):
            out.append(f"trigger: unknown kind “{t['on']}”")
        if t["on"] == "interval" and not (isinstance(t.get("minutes"), int) and t["minutes"] > 0):
            out.append(f"trigger interval ({t['run']}): needs minutes, a whole number above 0")
    for name, body in cfg.lists.items():
        job = body.get("on_new_entry")
        if job and job not in cfg.jobs.get("jobs", {}):
            out.append(f"list {name}: on_new_entry names unknown job “{job}”")
    return out


FIELDS = ["subject", "body", "sender.name", "sender.address", "sender.domain", "sender.registrable",
          "sender.mailbox", "reply_to.address", "to.names", "recipients", "labels"]


def catalogue(engine) -> list[dict]:
    """Every fact a rule can test: name, source, type, values (when they are known), cost, help."""
    out = []
    for name, spec in engine.config.facts.items():
        kind = next((k for k in DETECTORS if k in spec), "?")
        cost = "lookup" if kind == "lookup" else "free"
        values = None
        if kind == "derived":
            vals = [c.get("value", True) for c in spec["derived"]]
            if all(isinstance(v, (bool, type(None))) for v in vals):
                typ = "bool"
            elif all(isinstance(v, str) and not v.startswith("$") for v in vals if v is not None):
                typ, values = "choice", [v for v in vals if v is not None]
            else:
                typ = "value"
        elif kind in ("any", "all", "header") or (kind == "pattern" and spec.get("returns", "bool") == "bool") \
                or (kind == "list" and spec["list"].get("returns", "bool") == "bool") \
                or (kind == "addresses" and spec["addresses"].get("as") == "bool"):
            typ = "bool"
        elif kind == "lookup":
            typ = "number"
        else:
            typ = "value"
        out.append({"name": name, "source": kind, "type": typ, "values": values, "cost": cost,
                    "help": spec.get("help", "")})
    for name, q in engine.jev.questions.items():
        gated = isinstance(q.get("ask"), dict)
        if q["type"] == "yes_no":
            out.append({"name": name, "source": "jev", "type": "bool", "values": None,
                        "cost": "jev (2nd request)" if gated else "jev", "help": q["question"]})
        else:
            opts = None if q.get("options_from") else list(engine.jev.options(q, None))
            out.append({"name": name, "source": "jev", "type": "choice", "values": opts,
                        "cost": "jev (2nd request)" if gated else "jev",
                        "help": q["question"] if isinstance(q["question"], str) else ""})
    out += [{"name": "labelled", "source": "state", "type": "bool", "values": None, "cost": "free",
             "help": "the label stage gave the email at least one label"}]
    out += [{"name": f, "source": "field", "type": "value", "values": None, "cost": "free", "help": "email field"}
            for f in FIELDS]
    return out
