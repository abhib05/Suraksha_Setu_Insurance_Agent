"""Discovery agent: the deterministic matchers find covers; for weak evidence the model adds
a note explaining the passbook line to the reviewer. It never upgrades evidence."""

import json
import logging
from collections.abc import Callable
from typing import Literal

from pydantic import BaseModel

from suraksha.contracts import CaseFacts, Entitlement, ExtractedDocument, Strength
from suraksha.services.rules.matchers import match_entitlements, transactions
from suraksha.tools import llm
from suraksha.tools.prompts import load_prompt

log = logging.getLogger("suraksha.discovery")


class LineNote(BaseModel):
    likely: Literal["likely", "unclear", "unlikely"]
    reason: str


def discover(
    facts: CaseFacts,
    docs: list[ExtractedDocument],
    *,
    use_model: bool = True,
    complete_json: Callable | None = None,
) -> tuple[list[Entitlement], dict[str, str]]:
    """Covers found, plus reviewer notes keyed by entitlement id (weak evidence only)."""
    ents = match_entitlements(facts, docs)
    notes: dict[str, str] = {}
    if not use_model:
        return ents, notes
    prompt = load_prompt("discovery")
    complete_json = complete_json or llm.complete_json
    context = [t.raw for t in transactions(docs)][-20:]
    for e in ents:
        if e.strength is not Strength.POSSIBLE or e.scheme_id.value == "rupay":
            continue
        try:
            note, _ = complete_json("discovery", [
                {"role": "system", "content": prompt.text},
                {"role": "user", "content": json.dumps({
                    "scheme": e.scheme_id.value.upper(),
                    "matched_lines": [ev.snippet for ev in e.evidence],
                    "other_lines": context})},
            ], LineNote)  # fmt: skip
            notes[e.entitlement_id] = f"{note.likely}: {note.reason}"
        except Exception as err:
            log.info("discovery note skipped (%s)", type(err).__name__)
    return ents, notes
