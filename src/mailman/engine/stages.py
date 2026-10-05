"""Rules (config/rules.yaml): stages run in order; each holds rules with a condition and a result.

A stage:
  match      first — the first rule that applies decides;  all — every rule that applies adds its label
  on_match   stop — no later stage runs, except stages marked `always`
  only_if    a condition on the state so far (e.g. {labelled: true})
  always     runs even after a stop; its outcome replaces the one reached ("rescued from …")
A rule:  name, note, when, unless, and `then: <outcome>` or `label: <Gmail label>`;
         `leftover: true` — in an `all` stage, applies only when no rule before it did.
State facts for conditions: `labelled`, `label_<name>` (lower-case), `outcome`.
"""

from __future__ import annotations

from mailman.engine.facts import Facts


def applies(rule: dict, facts: Facts) -> bool:
    return facts.matches(rule["when"]) and not ("unless" in rule and facts.matches(rule["unless"]))


def decide(rules: dict, facts: Facts) -> tuple[str, str, list[str]]:
    """(outcome, explanation, labels)."""
    outcome, why, labels, stopped = rules.get("default_outcome", "keep"), "", [], False
    for stage in rules["stages"]:
        if stopped and not stage.get("always"):
            continue
        facts.state.update({"labelled": bool(labels), "outcome": outcome,
                            **{f"label_{n.lower()}": True for n in labels}})
        facts.values = {k: v for k, v in facts.values.items() if k not in facts.state}
        if "only_if" in stage and not facts.matches(stage["only_if"]):
            continue
        items = [r for r in rules.get(stage["name"], []) if r.get("enabled", True)]
        if stage["match"] == "all":
            hits = []
            for r in items:
                if not (r.get("leftover") and hits) and applies(r, facts):
                    hits.append(r)
            labels = list(dict.fromkeys([*labels, *(r["label"] for r in hits if "label" in r)]))
            names = ", ".join(r["name"] for r in hits)
            why = "; ".join(x for x in (why, names) if x)
            continue
        hit = next((r for r in items if applies(r, facts)), None)
        if not hit:
            continue
        if stage.get("always"):
            if outcome != hit["then"]:
                why = f"{hit['name']} (rescued from {outcome}: {why})"
                outcome = hit["then"]
            continue
        outcome = hit["then"]
        why = "; ".join(x for x in (why, hit["name"]) if x)
        if stage.get("on_match") == "stop":
            stopped = True
    facts.state.update({"labelled": bool(labels), "outcome": outcome,
                        **{f"label_{n.lower()}": True for n in labels}})
    return outcome, why, labels
