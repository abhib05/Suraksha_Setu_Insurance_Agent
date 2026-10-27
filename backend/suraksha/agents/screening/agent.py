"""Screening: a Muse Spark call with its own prompt classifies family messages before the
conversation agent sees them (the Meta API has no Llama Guard; SRS 2.7)."""

from collections.abc import Callable

from pydantic import BaseModel

from suraksha.tools import llm
from suraksha.tools.prompts import load_prompt


class Screening(BaseModel):
    injection: bool
    distress: bool
    abuse: bool
    reason: str = ""

    @property
    def flagged(self) -> bool:
        return self.injection or self.distress or self.abuse


def screen_text(text: str, *, complete_json: Callable | None = None) -> Screening:
    prompt = load_prompt("screening")
    complete_json = complete_json or llm.complete_json  # looked up per call (tests swap it)
    messages = [
        {"role": "system", "content": prompt.text},
        {"role": "user", "content": f"Text to classify:\n<<<\n{text}\n>>>"},
    ]
    verdict, _ = complete_json("screening", messages, Screening)
    return verdict
