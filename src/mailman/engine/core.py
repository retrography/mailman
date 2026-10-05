"""The engine: configuration in, a decision per email out."""

from __future__ import annotations

import asyncio

from mailman import gmail
from mailman.engine import stages
from mailman.engine.config import Config
from mailman.engine.facts import Facts, NeedJev
from mailman.engine.jevq import Jev
from mailman.engine.lists import Lists
from mailman.engine.mail import Email


class Engine:
    def __init__(self, config: Config | None = None):
        self.config = config or Config()
        self.refresh()

    def refresh(self) -> None:
        self.lists = Lists(self.config.lists, self.config.settings)
        self.jev = Jev(self.config)

    def reload(self) -> None:
        self.config.load()
        self.refresh()

    # ------------------------------------------------------------ one email

    def parse(self, raw: dict) -> Email:
        html = self.config.settings["email"]["html"]
        return Email(gmail.parse(raw, html_tags_in_plain=html["plain_is_html_at_tags"],
                                 stub_ratio=html["prefer_when_plain_is_stub"]["ratio"],
                                 stub_min_words=html["prefer_when_plain_is_stub"]["min_words"]),
                     self.config.settings)

    def facts(self, email: Email, answers: dict | None = None, lookups=None) -> Facts:
        return Facts(self, email, answers, lookups)

    def decide(self, facts: Facts) -> tuple[str, str, list[str]]:
        return stages.decide(self.config.rules, facts)

    async def classify(self, client, email: Email, lookups=None, sem: asyncio.Semaphore | None = None) -> dict:
        """Everything known about one email plus the decision. Jev is asked only when a rule reads an
        answer: the first request when any first-request answer is needed, the second (the gated questions
        whose gate opens) only when a gated answer is needed. A gated question whose gate stays shut has no
        answer."""
        facts = self.facts(email, lookups=lookups)
        facts.ask_jev = True
        probs: dict = {}
        tokens = 0
        while True:
            try:
                outcome, rule, labels = self.decide(facts)
                break
            except NeedJev as need:
                first = self.jev.first()
                if not all(n in facts.answers for n in first):
                    answers, probs, t = await self.jev.request(client, facts, first, sem)
                else:
                    gated = self.jev.gated(probs)
                    answers, t = {}, 0
                    if gated:
                        answers, _, t = await self.jev.request(client, facts, gated, sem)
                    answers = {**{n: None for n in self.jev.questions if n not in first}, **answers}
                if need.args[0] not in {**facts.answers, **answers}:
                    answers[need.args[0]] = None
                facts.answers.update(answers)
                tokens += t
                facts.values.clear()
                facts.state.clear()
        return {"email": email, "jev": {k: v for k, v in facts.answers.items() if v is not None},
                "facts": facts.logged(), "decision": outcome, "rule": rule, "labels": labels, "tokens": tokens}
