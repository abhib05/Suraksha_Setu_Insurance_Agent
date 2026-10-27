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

from suraksha.config import REPO_ROOT
from suraksha.contracts import (
    Attachment,
    CaseFacts,
    ChecklistItem,
    ClaimPacket,
    DocType,
    Entitlement,
    ExtractedDocument,
    FilledForm,
    Finding,
    FormField,
    RuleResult,
    RuleStatus,
    SchemeId,
    Severity,
    SourceRef,
    Strength,
    SubmissionReceipt,
    VerifierVerdict,
)
from suraksha.contracts.submission import Channel
from suraksha.services import rules
from suraksha.services.registry import expected_payout, required_documents
from suraksha.services.rules.facts import rule_facts
from suraksha.services.rules.matchers import match_entitlements

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
    paid_amount: dict[SchemeId, int] = field(default_factory=dict)  # default: full payout


class StubAgents:
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

    # discovery agent: the deterministic matchers (M5 adds model reasoning on top)
    def discover(self, facts: CaseFacts, docs: list[ExtractedDocument]) -> list[Entitlement]:
        self._maybe_fail("discover")
        ents = match_entitlements(facts, docs)
        return [e.model_copy(update={"strength": Strength.POSSIBLE})
                if e.scheme_id in self.s.weak_evidence else e for e in ents]  # fmt: skip

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

    # verifier agent
    def verify_entitlements(
        self, case_id: str, ents: list[Entitlement], results: list[RuleResult]
    ) -> VerifierVerdict:
        self._maybe_fail("verify_entitlements")
        findings = [
            Finding(severity=Severity.WARNING, target=f"entitlement:{e.entitlement_id}",
                    reason="Only weak evidence for this cover; a reviewer should confirm.")
            for e in ents if e.strength is Strength.POSSIBLE
        ]  # fmt: skip
        return VerifierVerdict(subject_id=f"{case_id}:discovery", passed=True, findings=findings)

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

    # filing agent
    def build_packet(
        self, claim_id: str, ent: Entitlement, version: int, reviewer_comment: str | None
    ) -> ClaimPacket:
        self._maybe_fail("build_packet")
        d = ent.case_id
        src = lambda ref: SourceRef(kind="document_field", ref=ref)  # noqa: E731
        return ClaimPacket(
            claim_id=claim_id, scheme_id=ent.scheme_id, version=version,
            forms=[FilledForm(template_id=f"{ent.scheme_id.value}-claim-form", fields=[
                FormField(name="date_of_death", value="2026-09-14",
                          source_ref=src(f"{d}-d2#date_of_death")),
                FormField(name="nominee_name", value="tok_stub_spouse",
                          source_ref=SourceRef(kind="family_statement", ref=f"{d}-c1")),
            ])],
            attachments=[Attachment(doc_id=f"{d}-d{i}", label=lbl, order=i)
                         for i, lbl in enumerate(["Passbook", "Death certificate"], start=1)],
            cover_letter="To the Branch Manager, Kaveri Gramin Bank ..."
            + (f"\n(Revised: {reviewer_comment})" if reviewer_comment else ""),
            checklist=[ChecklistItem(item="Claim form signed by nominee", done=False)],
        )  # fmt: skip

    def verify_packet(self, packet: ClaimPacket) -> VerifierVerdict:
        self._maybe_fail("verify_packet")
        findings = [
            Finding(severity=Severity.BLOCKER, target=t, reason="field has no source")
            for t in packet.untraced_fields()
        ]
        if packet.version in self.s.verifier_fails_versions:
            findings.append(
                Finding(
                    severity=Severity.BLOCKER,
                    target="date_of_death",
                    reason="Date on form differs from the death certificate.",
                )
            )
        return VerifierVerdict(
            subject_id=packet.idempotency_key, passed=not findings, findings=findings
        )

    # submission service (real adapter in M6)
    def submit(self, packet: ClaimPacket) -> SubmissionReceipt:
        self._maybe_fail("submit")
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
