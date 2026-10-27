"""Case coordinator: chooses the claim's next allowed action (code) and writes the reviewer
summary (model, with a plain fallback). The workflow only accepts the actions listed in
ALLOWED, so the model cannot move a claim anywhere it should not go."""

import json
import logging
from collections.abc import Callable

from pydantic import BaseModel

from suraksha.contracts import (
    Action,
    ClaimPacket,
    NextAction,
    RuleResult,
    Severity,
    VerifierVerdict,
)
from suraksha.tools import llm
from suraksha.tools.prompts import load_prompt

log = logging.getLogger("suraksha.coordinator")

# After a packet is verified these are the only moves the workflow allows.
ALLOWED_AFTER_VERIFY = {Action.REQUEST_APPROVAL, Action.ESCALATE_TO_HUMAN}


class Summary(BaseModel):
    summary: str


def plain_summary(packet: ClaimPacket, verdict: VerifierVerdict, result: RuleResult) -> str:
    scheme = packet.scheme_id.value.upper()
    issues = [f.reason for f in verdict.findings if f.severity is not Severity.INFO]
    open_items = [c.item for c in packet.checklist if not c.done and c.item.startswith("Missing")]
    parts = [f"{scheme} claim, packet v{packet.version}: eligibility "
             f"{result.status.value} under {', '.join(result.rule_ids)}."]  # fmt: skip
    parts.append("Check: " + " ".join(issues) if issues else "No issues found by the verifier.")
    if open_items:
        parts.append("Open items: " + "; ".join(open_items) + ".")
    parts.append("After approval the packet goes to the bank; the nominee signs the form.")
    return " ".join(parts)


def next_action(
    packet: ClaimPacket,
    verdict: VerifierVerdict,
    result: RuleResult,
    *,
    use_model: bool = True,
    complete_json: Callable | None = None,
) -> NextAction:
    action = Action.REQUEST_APPROVAL if verdict.passed else Action.ESCALATE_TO_HUMAN
    reason = ("Verifier passed; a human must approve before filing." if verdict.passed
              else "Verifier found blocking problems; a reviewer must resolve them.")  # fmt: skip
    summary = plain_summary(packet, verdict, result)
    if use_model:
        try:
            out, _ = (complete_json or llm.complete_json)("coordinator", [
                {"role": "system", "content": load_prompt("coordinator").text},
                {"role": "user", "content": json.dumps({
                    "scheme": packet.scheme_id.value, "version": packet.version,
                    "eligibility": result.model_dump(mode="json"),
                    "verifier": verdict.model_dump(mode="json"),
                    "checklist": [c.model_dump() for c in packet.checklist]})},
            ], Summary)  # fmt: skip
            if out.summary.strip() and "will be paid" not in out.summary.lower():
                summary = out.summary.strip()
        except Exception as e:
            log.info("summary model unavailable (%s); using plain summary", type(e).__name__)
    assert action in ALLOWED_AFTER_VERIFY
    return NextAction(action=action, reason=reason, summary=summary)
