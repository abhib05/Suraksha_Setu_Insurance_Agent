"""Scheme registry: payouts, channels, documents, time rules and sources, one YAML per scheme."""

from datetime import date
from functools import lru_cache
from typing import Any

import yaml

from suraksha.config import REPO_ROOT
from suraksha.contracts import DocType, SchemeId

REGISTRY_DIR = REPO_ROOT / "registry"


@lru_cache
def load_scheme(scheme: SchemeId | str) -> dict[str, Any]:
    sid = SchemeId(scheme).value
    with (REGISTRY_DIR / f"{sid}.yaml").open(encoding="utf-8") as f:
        return yaml.safe_load(f)


def required_documents(scheme: SchemeId) -> list[DocType]:
    return [DocType(d) for d in load_scheme(scheme)["required_documents"]]


def expected_payout(scheme: SchemeId, card_issue_date: str | None = None) -> int:
    reg = load_scheme(scheme)
    tiers = reg.get("payout_by_card_issue_date")
    if tiers and card_issue_date:
        issued = date.fromisoformat(card_issue_date)
        for t in tiers:
            if (before := t.get("issued_on_or_before")) and issued <= date.fromisoformat(before):
                return t["payout_inr"]
            if (after := t.get("issued_after")) and issued > date.fromisoformat(after):
                return t["payout_inr"]
    return reg["payout_inr"]


def premium_amounts(scheme: SchemeId) -> set[int]:
    p = load_scheme(scheme).get("premium") or {}
    return {p["annual_inr"], *p.get("pro_rata_inr", []), *p.get("legacy_annual_inr", [])}


def passbook_markers(scheme: SchemeId) -> list[str]:
    return (load_scheme(scheme).get("premium") or {}).get("passbook_markers", [])
