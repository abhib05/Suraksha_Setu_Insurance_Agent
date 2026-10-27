"""Deterministic matchers: passbook debits and card records -> covers the deceased held.

Passbook lines arrive from the document agent as ExtractedDocument fields named `txn` with
value "YYYY-MM-DD|description|amount|DR" (or CR). The discovery agent runs these first and
only reasons about what they cannot settle.
"""

import re
from dataclasses import dataclass
from datetime import date

from suraksha.contracts import (
    AssetType,
    CaseFacts,
    DocType,
    Entitlement,
    Evidence,
    ExtractedDocument,
    SchemeId,
    Strength,
)
from suraksha.services.registry import expected_payout, passbook_markers, premium_amounts

GENERIC_INSURANCE = re.compile(r"\b(INS|INSUR\w*|PREM\w*|BIMA|LIC|POLICY)\b", re.I)
CARD_CHANNEL = re.compile(r"\b(ATM|POS|RUPAY|ECOM|E-COM|MICRO ATM|CARD|AEPS|BC)\b", re.I)


@dataclass(frozen=True)
class Txn:
    doc_id: str
    raw: str
    on: date
    description: str
    amount: float
    debit: bool


def transactions(docs: list[ExtractedDocument]) -> list[Txn]:
    out = []
    for d in docs:
        if d.doc_type not in (DocType.PASSBOOK, DocType.BANK_STATEMENT):
            continue
        for f in d.fields:
            if f.name != "txn" or not f.value:
                continue
            try:
                on, desc, amount, side = (p.strip() for p in f.value.split("|"))
                out.append(
                    Txn(
                        d.doc_id,
                        f.value,
                        date.fromisoformat(on),
                        desc,
                        float(amount.replace(",", "")),
                        side.upper().startswith("D"),
                    )
                )
            except ValueError:
                continue  # unreadable line; the document agent flags it separately
    return sorted(out, key=lambda t: t.on)


@dataclass(frozen=True)
class PremiumHit:
    txn: Txn
    strength: Strength


def premium_debits(txns: list[Txn], scheme: SchemeId) -> list[PremiumHit]:
    """Debits of a known premium amount. Strong when the line names the scheme; possible
    when it only looks like an insurance debit of the right amount."""
    amounts = premium_amounts(scheme)
    markers = [m.upper() for m in passbook_markers(scheme)]
    hits = []
    for t in txns:
        if not t.debit or round(t.amount) not in amounts:
            continue
        desc = t.description.upper()
        if any(m in desc for m in markers):
            hits.append(PremiumHit(t, Strength.STRONG))
        elif GENERIC_INSURANCE.search(desc):
            hits.append(PremiumHit(t, Strength.POSSIBLE))
    return hits


def last_card_use(txns: list[Txn], on_or_before: date | None) -> Txn | None:
    used = [t for t in txns if CARD_CHANNEL.search(t.description)
            and (on_or_before is None or t.on <= on_or_before)]  # fmt: skip
    return used[-1] if used else None


def match_entitlements(facts: CaseFacts, docs: list[ExtractedDocument]) -> list[Entitlement]:
    """The covers the evidence points to, each with the lines that prove it."""
    txns = transactions(docs)
    death = facts.deceased.date_of_death
    bank = next((a for a in facts.assets if a.type is AssetType.BANK_ACCOUNT), None)
    found: list[Entitlement] = []

    for scheme in (SchemeId.PMJJBY, SchemeId.PMSBY):
        hits = [h for h in premium_debits(txns, scheme) if death is None or h.txn.on <= death]
        if not hits:
            continue
        strength = Strength.STRONG if any(h.strength is Strength.STRONG for h in hits) else (
            Strength.POSSIBLE)  # fmt: skip
        found.append(Entitlement(
            entitlement_id=f"{facts.case_id}-e-{scheme.value}", case_id=facts.case_id,
            scheme_id=scheme, asset_id=bank.asset_id if bank else "unknown",
            evidence=[Evidence(doc_id=h.txn.doc_id, field="txn", snippet=h.txn.raw)
                      for h in hits[-2:]],
            strength=strength, expected_payout_inr=expected_payout(scheme),
        ))  # fmt: skip

    for card in (a for a in facts.assets if a.type is AssetType.DEBIT_CARD):
        variant = (card.card_variant or "").lower()
        if "rupay" not in variant and "pmjdy" not in variant:
            continue
        evidence = []
        use = last_card_use(txns, death)
        if use:
            evidence.append(Evidence(doc_id=use.doc_id, field="txn", snippet=use.raw))
        if card.last_txn_date:
            evidence.append(
                Evidence(
                    doc_id=card.source,
                    field="last_txn_date",
                    snippet=card.last_txn_date.isoformat(),
                )
            )
        issued = card.card_issue_date.isoformat() if card.card_issue_date else None
        found.append(Entitlement(
            entitlement_id=f"{facts.case_id}-e-rupay", case_id=facts.case_id,
            scheme_id=SchemeId.RUPAY, asset_id=card.asset_id,
            evidence=evidence or [Evidence(doc_id=card.source, field="card_variant",
                                           snippet=card.card_variant or "")],
            strength=Strength.STRONG if "pmjdy" in variant else Strength.POSSIBLE,
            expected_payout_inr=expected_payout(SchemeId.RUPAY, issued),
        ))  # fmt: skip
        break  # only one RuPay card per cardholder can be claimed
    return found
