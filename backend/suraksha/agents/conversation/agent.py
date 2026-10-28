"""Conversation agent: one chat turn -> reply + intake updates.

The model is asked nicely; the code makes sure. Before consent no update is kept. A reply
that promises money is regenerated once, then replaced with a safe line. Names are never
stored as typed: services.intake tokenises them into the vault.
"""

import json
import re
from collections.abc import Callable
from datetime import date
from typing import Literal

from pydantic import BaseModel, field_validator

from suraksha.tools import llm
from suraksha.tools.prompts import load_prompt

EventKind = Literal["accident", "illness", "natural", "suicide", "unknown"]
CardKind = Literal["rupay_pmjdy", "rupay", "other", "none", "unknown"]


class IntakeUpdates(BaseModel):
    speaker_name: str | None = None
    speaker_relationship: str | None = None
    deceased_name: str | None = None
    date_of_death: date | None = None
    event_type: EventKind | None = None
    event_description: str | None = None
    place_of_death: str | None = None
    age_at_death: int | None = None
    nominee_name: str | None = None
    nominee_relationship: str | None = None
    bank_name: str | None = None
    has_debit_card: bool | None = None
    card_kind: CardKind | None = None
    card_last_used: date | None = None
    card_issue_date: date | None = None

    @field_validator("age_at_death")
    @classmethod
    def _sane_age(cls, v: int | None) -> int | None:
        return v if v is None or 0 <= v <= 120 else None


class ConversationTurn(BaseModel):
    reply: str
    updates: IntakeUpdates = IntakeUpdates()
    intake_complete: bool = False
    wants_human: bool = False


PROMISE = re.compile(
    r"\b(you will (get|receive)|will be (paid|approved)|guarantee\w*|definitely (get|receive)|"
    r"(rs\.?|₹|inr)\s?[\d,]+\s*(is|will be)\s*(yours|paid))",
    re.I,
)
SAFE_PROMISE_FALLBACK = (
    "I can't promise any amount, but I will help you check every cover that may apply, and "
    "a reviewer will confirm before anything is filed. Shall we continue?"
)
MAX_HISTORY = 24


def promises_money(text: str) -> bool:
    return bool(PROMISE.search(text))


def take_turn(
    history: list[dict[str, str]],
    user_message: str,
    *,
    known: dict,
    consent_given: bool,
    complete_json: Callable | None = None,
    today: date | None = None,
) -> tuple[ConversationTurn, str]:
    """Returns the checked turn and the prompt version used."""
    prompt = load_prompt("conversation")
    complete_json = complete_json or llm.complete_json  # looked up per call (tests swap it)
    context = (
        f"Consent status: {'GIVEN' if consent_given else 'NOT YET GIVEN'}.\n"
        f"What we already know (do not ask again): {json.dumps(known, default=str)}\n"
        f"Today's date: {(today or date.today()).isoformat()}"
    )
    messages = [
        {"role": "system", "content": prompt.text},
        {"role": "system", "content": context},
        *history[-MAX_HISTORY:],
        {"role": "user", "content": user_message},
    ]
    turn, _ = complete_json("conversation", messages, ConversationTurn)
    if promises_money(turn.reply):
        fix = [*messages, {"role": "assistant", "content": turn.reply},
               {"role": "system", "content": "That reply promised money or an outcome, which "
                "is not allowed. Rewrite it without any promise."}]  # fmt: skip
        turn, _ = complete_json("conversation", fix, ConversationTurn)
        if promises_money(turn.reply):
            turn = turn.model_copy(update={"reply": SAFE_PROMISE_FALLBACK})
    if not consent_given:
        turn = turn.model_copy(update={"updates": IntakeUpdates(), "intake_complete": False})
    return turn, prompt.version
