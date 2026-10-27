"""Rules engine: deterministic eligibility per scheme, evaluated as of the date of death.

Each scheme's result is the conjunction of its active rules: any rule that fails makes it
not eligible (citing that rule); otherwise any rule that cannot be decided makes it unknown
(listing exactly the facts that would decide it); otherwise eligible, citing every rule.
"""

import json
from dataclasses import dataclass
from datetime import date
from functools import lru_cache
from pathlib import Path
from typing import Any

from suraksha.config import REPO_ROOT
from suraksha.contracts import Entitlement, RuleResult, RuleStatus, SchemeId
from suraksha.services.registry import load_scheme
from suraksha.services.rules.facts import question_for
from suraksha.services.rules.logic import evaluate, is_unknown

RULES_DIR = REPO_ROOT / "rules"


@dataclass(frozen=True)
class Rule:
    id: str
    scheme: str
    version: str
    effective_from: str
    effective_to: str | None
    source: str
    title: str
    requires: tuple[str, ...]
    logic: Any
    explain_pass: str
    explain_fail: str

    def active_on(self, day: date) -> bool:
        start = date.fromisoformat(self.effective_from)
        end = date.fromisoformat(self.effective_to) if self.effective_to else date.max
        return start <= day <= end


@dataclass(frozen=True)
class RuleOutcome:
    rule: Rule
    status: RuleStatus
    missing: list[str]


@lru_cache
def load_rules(rules_dir: Path = RULES_DIR) -> tuple[Rule, ...]:
    rules = []
    for path in sorted(rules_dir.glob("*.json")):
        d = json.loads(path.read_text(encoding="utf-8"))
        rules.append(Rule(
            id=d["id"], scheme=d["scheme"], version=d["version"],
            effective_from=d["effective_from"], effective_to=d.get("effective_to"),
            source=d["source"], title=d["title"], requires=tuple(d["requires"]),
            logic=d["logic"], explain_pass=d["explain"]["pass"], explain_fail=d["explain"]["fail"],
        ))  # fmt: skip
    return tuple(rules)


def evaluate_rule(rule: Rule, facts: dict[str, Any]) -> RuleOutcome:
    ev = evaluate(rule.logic, facts)
    if is_unknown(ev.value):
        return RuleOutcome(rule, RuleStatus.UNKNOWN, ev.missing)
    return RuleOutcome(rule, RuleStatus.ELIGIBLE if ev.value else RuleStatus.NOT_ELIGIBLE, [])


def check(scheme: SchemeId, facts: dict[str, Any], entitlement_id: str) -> RuleResult:
    """Eligibility for one scheme from flat facts (see services.rules.facts)."""
    if not facts.get("date_of_death"):
        as_of = date.today()  # rules are dated by the death; without it, use today's set
    else:
        as_of = date.fromisoformat(str(facts["date_of_death"]))
    active = [r for r in load_rules() if r.scheme == scheme.value and r.active_on(as_of)]
    if not active:
        raise LookupError(f"no rules for {scheme.value} active on {as_of}")
    outcomes = [evaluate_rule(r, facts) for r in active]
    version = load_scheme(scheme)["ruleset_version"]

    failed = [o for o in outcomes if o.status is RuleStatus.NOT_ELIGIBLE]
    unknown = [o for o in outcomes if o.status is RuleStatus.UNKNOWN]
    if failed:
        return RuleResult(
            entitlement_id=entitlement_id, status=RuleStatus.NOT_ELIGIBLE,
            rule_ids=[o.rule.id for o in failed], rule_version=version,
            explanation_en=" ".join(o.rule.explain_fail for o in failed),
        )  # fmt: skip
    if unknown:
        missing = sorted({m for o in unknown for m in o.missing})
        return RuleResult(
            entitlement_id=entitlement_id, status=RuleStatus.UNKNOWN,
            rule_ids=[o.rule.id for o in unknown], rule_version=version,
            missing_facts=missing,
            explanation_en="To confirm this cover we still need "
            + "; ".join(dict.fromkeys(question_for(m) for m in missing)) + ".",
        )  # fmt: skip
    return RuleResult(
        entitlement_id=entitlement_id, status=RuleStatus.ELIGIBLE,
        rule_ids=[o.rule.id for o in outcomes], rule_version=version,
        explanation_en=" ".join(o.rule.explain_pass for o in outcomes),
    )  # fmt: skip


def check_entitlement(ent: Entitlement, facts: dict[str, Any]) -> RuleResult:
    return check(ent.scheme_id, facts, ent.entitlement_id)


def explain_rule(rule_id: str) -> Rule:
    return next(r for r in load_rules() if r.id == rule_id)
