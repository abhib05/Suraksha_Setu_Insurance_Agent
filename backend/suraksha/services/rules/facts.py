"""CaseFacts + documents (+ answers the family gave later) -> the flat facts rules read.

A fact is only set when the evidence settles it. When it cannot (for example, when someone
first joined a scheme, if only this year's debit is visible), it stays missing and the
rule comes back unknown with a question for the family instead of a guess.
"""

from datetime import date
from typing import Any

from suraksha.contracts import AssetType, CaseFacts, ExtractedDocument, SchemeId
from suraksha.services.rules.matchers import last_card_use, premium_debits, transactions

QUESTIONS = {
    "date_of_death": "the date of death",
    "event_type": "whether the death was caused by an accident",
    "age_at_death": "their age at the time of death",
    "pmjjby_premium_date": "a passbook page showing the Rs 436 PMJJBY debit",
    "pmjjby_premium_inr": "a passbook page showing the Rs 436 PMJJBY debit",
    "pmjjby_enrolment_date": "when they first joined PMJJBY (was this their first year?)",
    "pmsby_premium_date": "a passbook page showing the Rs 20 PMSBY debit",
    "pmsby_premium_inr": "a passbook page showing the Rs 20 PMSBY debit",
    "pmsby_enrolment_date": "when they first joined PMSBY (was this their first year?)",
    "card_last_txn_date": "the last date the RuPay card was used (a recent passbook page or "
    "bank statement shows this)",
    "card_issue_date": "when the RuPay card was issued",
}


def _cover_year(d: date) -> int:
    """Cover years start on 1 June; a May renewal debit belongs to the year it renews."""
    return d.year if d.month >= 5 else d.year - 1


def rule_facts(
    facts: CaseFacts, docs: list[ExtractedDocument], extra: dict[str, Any] | None = None
) -> dict[str, Any]:
    d = facts.deceased
    death = d.date_of_death
    out: dict[str, Any] = {
        "event_type": d.event_type.value if d.event_type.value != "unknown" else None,
        "date_of_death": death.isoformat() if death else None,
        "age_at_death": d.age_at_death,
    }
    txns = transactions(docs)

    for scheme in (SchemeId.PMJJBY, SchemeId.PMSBY):
        s = scheme.value
        debits = [h.txn for h in premium_debits(txns, scheme) if death is None or h.txn.on <= death]
        if not debits:
            continue
        latest = debits[-1]
        out[f"{s}_premium_date"] = latest.on.isoformat()
        out[f"{s}_premium_inr"] = round(latest.amount)
        earliest = debits[0]
        spans_years = _cover_year(earliest.on) < _cover_year(latest.on)
        if latest.on.month not in (5, 6):
            # Renewals are auto-debited at the end of May (remitted by 30 June). A premium
            # taken in any other month is a new enrolment or a reinstatement, and both start
            # a fresh 30-day lien (PMJJBY rules paras 4 and 9(4)).
            out[f"{s}_enrolment_date"] = latest.on.isoformat()
        elif spans_years or (death and (death - earliest.on).days >= 30):
            # Enrolment was on or before the earliest debit we can see; that bound is
            # enough to clear the 30-day lien, so record it.
            out[f"{s}_enrolment_date"] = earliest.on.isoformat()

    card = next((a for a in facts.assets if a.type is AssetType.DEBIT_CARD), None)
    if card:
        passbook_use = last_card_use(txns, death)
        uses = [card.last_txn_date, passbook_use.on if passbook_use else None]
        uses = [u for u in uses if u and (death is None or u <= death)]
        if uses:
            out["card_last_txn_date"] = max(uses).isoformat()
        if card.card_issue_date:
            out["card_issue_date"] = card.card_issue_date.isoformat()

    for k, v in (extra or {}).items():
        if v not in (None, ""):
            out[k] = v
    return {k: v for k, v in out.items() if v is not None}


def question_for(fact: str) -> str:
    return QUESTIONS.get(fact, fact.replace("_", " "))
