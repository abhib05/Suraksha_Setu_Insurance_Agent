from enum import StrEnum

from pydantic import model_validator

from suraksha.contracts.common import Contract


class RuleStatus(StrEnum):
    ELIGIBLE = "eligible"
    NOT_ELIGIBLE = "not_eligible"
    UNKNOWN = "unknown"  # some required fact is missing; never a guess


class RuleResult(Contract):
    """Produced by the rules engine (deterministic, not a model)."""

    entitlement_id: str
    status: RuleStatus
    rule_ids: list[str]
    rule_version: str
    missing_facts: list[str] = []
    explanation_en: str  # plain words, shown to the family and the reviewer

    @model_validator(mode="after")
    def _unknown_names_missing_facts(self) -> "RuleResult":
        if self.status is RuleStatus.UNKNOWN and not self.missing_facts:
            raise ValueError("an unknown result must list its missing_facts")
        if self.status is not RuleStatus.UNKNOWN and self.missing_facts:
            raise ValueError("only an unknown result may list missing_facts")
        if not self.rule_ids:
            raise ValueError("every result must cite at least one rule id")
        return self
