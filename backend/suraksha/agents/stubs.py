"""Stub agents: the conversation, document, filing and verifier agents return a case
fixture from evals/cases (case A by default) until M4-M5 replace them. Discovery matching
and eligibility already run the real M3 services.

`Scenario` bends the stubs to exercise every workflow branch: unknown or not-eligible
rules, missing documents, verifier failures, weak evidence and flaky calls.
"""

import hashlib
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime

from suraksha.agents.coordinator import agent as coordinator
from suraksha.agents.discovery import agent as discovery
from suraksha.agents.filing import agent as filing
from suraksha.agents.verifier import agent as verifier
from suraksha.config import REPO_ROOT
from suraksha.contracts import (
    CaseFacts,
    ClaimPacket,
    DocType,
    Entitlement,
    ExtractedDocument,
    Finding,
    NextAction,
    RuleResult,
    RuleStatus,
    SchemeId,
    Severity,
    Strength,
    SubmissionReceipt,
    VerifierVerdict,
)
from suraksha.contracts.submission import Channel
from suraksha.services import rules
from suraksha.services.registry import expected_payout, required_documents
from suraksha.services.rules.facts import rule_facts
from suraksha.services.submission.portal import SubmissionRejected

CASES_DIR = REPO_ROOT / "evals" / "cases"


def _fixture(name: str, part: str, case_id: str):
    """Load evals/cases/<name>/<part>.json with the fixture's case id swapped for ours."""
    raw = (CASES_DIR / name / f"{part}.json").read_text(encoding="utf-8")
    return json.loads(raw.replace(name, case_id))


class TransientAgentError(RuntimeError):
    """Raised by a flaky stub; Temporal retries it."""


@dataclass
class Scenario:
    fixture: str = "case_a"
    rule_status: dict[SchemeId, RuleStatus] = field(default_factory=dict)
    # facts that must arrive (via facts_provided) before an unknown rule turns eligible
    missing_facts: dict[SchemeId, list[str]] = field(default_factory=dict)
    weak_evidence: set[SchemeId] = field(default_factory=set)
    # documents missing at first check; cleared one per `document_uploaded` signal
    missing_docs: dict[SchemeId, list[DocType]] = field(default_factory=dict)
    verifier_fails_versions: set[int] = field(default_factory=set)
    # activity name -> how many times it fails before succeeding
    flaky: dict[str, int] = field(default_factory=dict)
    portal_statuses: list[str] = field(default_factory=lambda: ["submitted", "under_review"])
    portal_rejects_versions: set[int] = field(default_factory=set)
    paid_amount: dict[SchemeId, int] = field(default_factory=dict)  # default: full payout


class StubAgents:
    use_model = False  # fixtures and deterministic agents only; LiveAgents turns models on

    def __init__(self, scenario: Scenario | None = None):
        self.s = scenario or Scenario()
        self._calls: dict[str, int] = {}
        self._uploads: dict[str, int] = {}
        self._polls: dict[str, int] = {}

    def _maybe_fail(self, name: str) -> None:
        n = self._calls[name] = self._calls.get(name, 0) + 1
        if n <= self.s.flaky.get(name, 0):
            raise TransientAgentError(f"{name}: simulated failure {n}")

    # conversation agent: the case fixture's interview answers
    def interview(self, case_id: str) -> CaseFacts:
        self._maybe_fail("interview")
        return CaseFacts.model_validate(_fixture(self.s.fixture, "facts", case_id))

    # document agent: the case fixture's extracted documents
    def extract_documents(self, case_id: str) -> list[ExtractedDocument]:
        self._maybe_fail("extract_documents")
        return [ExtractedDocument.model_validate(d)
                for d in _fixture(self.s.fixture, "documents", case_id)]  # fmt: skip

    # discovery agent (M5): matchers + reviewer notes for weak evidence
    def discover(
        self, facts: CaseFacts, docs: list[ExtractedDocument]
    ) -> tuple[list[Entitlement], dict[str, str]]:
        self._maybe_fail("discover")
        ents, notes = discovery.discover(facts, docs, use_model=self.use_model)
        ents = [e.model_copy(update={"strength": Strength.POSSIBLE})
                if e.scheme_id in self.s.weak_evidence else e for e in ents]  # fmt: skip
        return ents, notes

    # rules engine (real, M3); a Scenario override replaces it for branch tests
    def check_eligibility(
        self, ent: Entitlement, facts: CaseFacts, docs: list[ExtractedDocument],
        known_facts: dict[str, str],
    ) -> RuleResult:  # fmt: skip
        self._maybe_fail("check_eligibility")
        if ent.scheme_id not in self.s.rule_status:
            return rules.check_entitlement(ent, rule_facts(facts, docs, known_facts))
        status = self.s.rule_status[ent.scheme_id]
        missing = [f for f in self.s.missing_facts.get(ent.scheme_id, []) if f not in known_facts]
        if status is RuleStatus.UNKNOWN and not missing:
            status = RuleStatus.ELIGIBLE
        explain = {
            RuleStatus.ELIGIBLE: f"{ent.scheme_id.value.upper()} cover was active on that date.",
            RuleStatus.NOT_ELIGIBLE: "The cover had lapsed before the date of death.",
            RuleStatus.UNKNOWN: f"We still need: {', '.join(missing)}.",
        }[status]  # fmt: skip
        return RuleResult(
            entitlement_id=ent.entitlement_id, status=status,
            rule_ids=[f"{ent.scheme_id.value.upper()}-STUB"], rule_version="stub",
            explanation_en=explain, missing_facts=missing if status is RuleStatus.UNKNOWN else [],
        )  # fmt: skip

    # verifier agent (M5)
    def verify_entitlements(
        self, case_id: str, ents: list[Entitlement], docs: list[ExtractedDocument]
    ) -> VerifierVerdict:
        self._maybe_fail("verify_entitlements")
        return verifier.verify_entitlements(case_id, ents, docs)

    def brief(self, ents: list[Entitlement], results: list[RuleResult]) -> str:
        if not ents:
            return (
                "We could not find a cover from the details and documents so far. A "
                "reviewer will look at your case and may ask for more documents."
            )
        by_id = {r.entitlement_id: r for r in results}
        lines = []
        for e in ents:
            r = by_id[e.entitlement_id]
            amount = f"about Rs {e.expected_payout_inr:,}" if e.expected_payout_inr else ""
            lines.append(
                f"{e.scheme_id.value.upper()}: {r.status.value} {amount}. {r.explanation_en}"
            )
        return "\n".join(lines)

    def missing_documents(
        self, claim_id: str, scheme: SchemeId, docs: list[ExtractedDocument]
    ) -> list[DocType]:
        """Registry-required documents not yet held; each upload clears the next one."""
        self._maybe_fail("missing_documents")
        if scheme in self.s.missing_docs:
            missing = self.s.missing_docs[scheme]
        else:
            held = {d.doc_type for d in docs}
            missing = [t for t in required_documents(scheme) if t not in held]
        return missing[self._uploads.get(claim_id, 0) :]

    def document_uploaded(self, claim_id: str) -> None:
        self._uploads[claim_id] = self._uploads.get(claim_id, 0) + 1

    # filing agent (M5)
    def build_packet(
        self, claim_id: str, ent: Entitlement, facts: CaseFacts, docs: list[ExtractedDocument],
        version: int, reviewer_comment: str | None,
    ) -> ClaimPacket:  # fmt: skip
        self._maybe_fail("build_packet")
        return filing.build_packet(claim_id, ent, facts, docs, version, reviewer_comment,
                                   use_model=self.use_model)  # fmt: skip

    def verify_packet(
        self, packet: ClaimPacket, ent: Entitlement, facts: CaseFacts,
        docs: list[ExtractedDocument], rule_result: RuleResult,
    ) -> VerifierVerdict:  # fmt: skip
        self._maybe_fail("verify_packet")
        verdict = verifier.verify_packet(packet, ent, facts, docs, rule_result,
                                         use_model=self.use_model)  # fmt: skip
        if packet.version in self.s.verifier_fails_versions:
            seeded = Finding(
                severity=Severity.BLOCKER,
                target="date_of_death",
                reason="Date on form differs from the death certificate.",
            )
            verdict = verdict.model_copy(
                update={"passed": False, "findings": [*verdict.findings, seeded]}
            )
        return verdict

    # case coordinator (M5)
    def next_action(
        self, packet: ClaimPacket, verdict: VerifierVerdict, rule_result: RuleResult
    ) -> NextAction:
        return coordinator.next_action(packet, verdict, rule_result, use_model=self.use_model)

    # submission service (LiveAgents uses the portal adapter)
    def submit(self, packet: ClaimPacket) -> SubmissionReceipt:
        self._maybe_fail("submit")
        if packet.version in self.s.portal_rejects_versions:
            raise SubmissionRejected(f"portal rejected {packet.idempotency_key}: stamp missing")
        n = int(hashlib.sha256(packet.idempotency_key.encode()).hexdigest(), 16) % 100_000
        return SubmissionReceipt(
            claim_id=packet.claim_id,
            idempotency_key=packet.idempotency_key,
            channel=Channel.MOCK_PORTAL,
            ack_id=f"ACK-{n:05d}",
            claim_number=f"MOCK-{packet.scheme_id.value.upper()}-{n:05d}",
            submitted_at=datetime.now(UTC),
        )

    def claim_status(self, claim_number: str, scheme: SchemeId) -> tuple[str, int | None]:
        self._maybe_fail("claim_status")
        n = self._polls[claim_number] = self._polls.get(claim_number, 0) + 1
        statuses = self.s.portal_statuses
        if n <= len(statuses):
            return statuses[n - 1], None
        return "paid", self.s.paid_amount.get(scheme, expected_payout(scheme))
