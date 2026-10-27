from enum import StrEnum

from suraksha.contracts.common import Contract, Rupees, SchemeId


class Evidence(Contract):
    doc_id: str
    field: str
    snippet: str  # e.g. "PMJJBY PREM 436 01-06-2025"


class Strength(StrEnum):
    STRONG = "strong"
    POSSIBLE = "possible"  # weak evidence; the family is told it is a maybe


class Entitlement(Contract):
    """Produced by the discovery agent: a cover the deceased probably held."""

    entitlement_id: str
    case_id: str
    scheme_id: SchemeId
    asset_id: str
    evidence: list[Evidence]
    strength: Strength
    expected_payout_inr: Rupees | None = None
