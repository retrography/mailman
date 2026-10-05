"""Jev (config/jev.yaml): what Jev is shown, the questions, and when each is asked.

A question:
  type            choice | yes_no
  ask             first — in the first request; or {when: {answer, options, sum_at_least | sum_below}} —
                  in the second request, depending on the probabilities of a first answer
  question        the instruction text; with `context` (values Jev is given) the two are sent together
  options         {id: criteria}; or {use: <shared option set>, override: {id: criteria}}
  options_from    countries — all country codes; or {fact, id, text} — one option per item of a fact
  extra_options   options added after the generated ones
  answer_as       value — report the item behind a generated option instead of its id
  yes_at          for yes/no: p(yes) at or above this counts as yes (default: yes_threshold)
"""

from __future__ import annotations

import asyncio
import os
from contextlib import nullcontext

from typesafe_sdk import AsyncTypeSafeClient, Choice, Noul

from mailman.engine.facts import COUNTRY_CODES, Facts


class Jev:
    def __init__(self, config):
        self.cfg = config.jev
        self.settings = config.settings.get("jev", {})
        self.questions: dict = self.cfg["questions"]

    def yes_threshold(self, name: str) -> float:
        return self.questions[name].get("yes_at", self.cfg.get("yes_threshold", 0.5))

    def client(self) -> AsyncTypeSafeClient:
        base = os.environ.get("MAILMAN_TYPESAFE_BASE_URL", self.settings.get("base_url", "https://api.typesafe.ai"))
        return AsyncTypeSafeClient(base_url=base, model=self.settings.get("model", "jev-latest"))

    # ------------------------------------------------------------ what Jev sees

    def state(self, facts: Facts) -> dict:
        out = {}
        for key, spec in self.cfg["input"]["fields"].items():
            if isinstance(spec, str):
                out[key] = facts.get(spec)
                continue
            v = facts.get(spec["field"])
            if "max_chars" in spec and isinstance(v, str):
                v = v[:spec["max_chars"]]
            if not v and "fallback" in spec:
                v = facts.get(spec["fallback"])
            out[key] = v if v or "blank" not in spec else spec["blank"]
        wrap = self.cfg["input"].get("wrap")
        return {wrap: out} if wrap else out

    # ------------------------------------------------------------ questions

    def options(self, q: dict, facts: Facts) -> dict:
        opts: dict = {}
        src = q.get("options_from")
        if src == "countries":
            opts.update(COUNTRY_CODES)
        elif isinstance(src, dict):
            for i, v in enumerate(facts.get(src["fact"]) or []):
                opts[src["id"].format(i=i)] = src["text"].format(value=v)
        o = q.get("options") or {}
        if "use" in o:
            opts.update(self.cfg["option_sets"][o["use"]])
            opts.update(o.get("override", {}))
        else:
            opts.update(o)
        opts.update(q.get("extra_options", {}))
        return opts

    def build(self, name: str, facts: Facts):
        q = self.questions[name]
        instructions = {**q["context"], "question": q["question"]} if "context" in q else q["question"]
        if q["type"] == "yes_no":
            return Noul(instructions=instructions, criteria={"true": q["yes"], "false": q["no"]})
        return Choice(instructions=instructions, criteria=self.options(q, facts))

    def first(self) -> list[str]:
        return [n for n, q in self.questions.items() if q.get("ask", "first") == "first"]

    def gated(self, probabilities: dict[str, dict]) -> list[str]:
        """The second-request questions whose gate opens, given the first answers' probabilities."""
        out = []
        for n, q in self.questions.items():
            w = q["ask"].get("when") if isinstance(q.get("ask"), dict) else None
            if not w:
                continue
            p = sum(probabilities.get(w["answer"], {}).get(o, 0) for o in w["options"])
            if ("sum_at_least" in w and p >= w["sum_at_least"]) or ("sum_below" in w and p < w["sum_below"]):
                out.append(n)
        return out

    @staticmethod
    def summarize(resp, names: list[str]) -> dict:
        out = {}
        for n in names:
            if n in resp.nouls:
                out[n] = round(resp.nouls[n].noul, 3)
            else:
                a = resp.choices[n]
                top = sorted(a.probabilities.items(), key=lambda kv: -kv[1])[:3]
                out[n] = {"choice": a.choice, "conf": round(a.confidence, 3),
                          "top": {k: round(v, 3) for k, v in top}}
        return out

    def finish(self, answers: dict, facts: Facts) -> None:
        """A generated option → the item behind it (`answer_as: value`)."""
        for n, a in answers.items():
            src = self.questions[n].get("options_from")
            if a and self.questions[n].get("answer_as") == "value" and isinstance(src, dict):
                a["raw"] = a["choice"]
                ids = {src["id"].format(i=i): v for i, v in enumerate(facts.get(src["fact"]) or [])}
                a["choice"] = ids.get(a["choice"], a["choice"])

    async def request(self, client, facts: Facts, names: list[str], sem: asyncio.Semaphore | None = None):
        """One request to Jev: (answers, the choice probabilities, input tokens)."""
        asking, facts.ask_jev = facts.ask_jev, False   # building a question may read facts, never answers
        try:
            state, built = self.state(facts), {n: self.build(n, facts) for n in names}
        finally:
            facts.ask_jev = asking
        async with (sem or nullcontext()):
            resp = await client.system_one(state, built, model=self.settings.get("model", "jev-latest"))
        answers = self.summarize(resp, names)
        self.finish(answers, facts)
        return answers, {n: resp.choices[n].probabilities for n in names if n in resp.choices}, \
            resp.usage.input_tokens

    async def ask(self, client, facts: Facts, sem: asyncio.Semaphore | None = None,
                  only: list[str] | None = None) -> tuple[dict, int]:
        """Everything at once: the first request, then the gated questions whose gate opens. `only`: just
        these questions, in one request. (The engine itself asks lazily — see Engine.classify.)"""
        answers, probs, tokens = await self.request(client, facts, only or self.first(), sem)
        second = [] if only else self.gated(probs)
        if second:
            a2, _, t2 = await self.request(client, facts, second, sem)
            answers.update(a2)
            tokens += t2
        return answers, tokens
