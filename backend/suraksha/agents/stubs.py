"""Stub agents (M2): fixed contract objects for case A, so the workflows can be built and
tested before any model call exists. M3-M5 swap each method for the real agent or service.

`Scenario` bends the stubs to exercise every workflow branch: unknown or not-eligible
rules, missing documents, verifier failures, weak evidence and flaky calls.
"""

import hashlib
from dataclasses import dataclass, field
from datetime import UTC, date, datetime

from suraksha.contracts import (
    Asset,
    AssetType,
    Attachment,
    CaseFacts,
    ChecklistItem,
    Claimant,
    ClaimPacket,
    Deceased,
    DocType,
    Entitlement,
    EventType,
    Evidence,
    ExtractedDocument,
    ExtractedField,
    FilledForm,
    Finding,
    FormField,
    Relationship,
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

PAYOUT = {SchemeId.PMJJBY: 200_000, SchemeId.PMSBY: 200_000, SchemeId.RUPAY: 200_000}
RULES = {SchemeId.PMJJBY: "PMJJBY-R1", SchemeId.PMSBY: "PMSBY-R1", SchemeId.RUPAY: "RUPAY-R1"}
REQUIRED_DOCS = {
    SchemeId.PMJJBY: [DocType.DEATH_CERTIFICATE, DocType.PASSBOOK],
    SchemeId.PMSBY: [DocType.DEATH_CERTIFICATE, DocType.PASSBOOK, DocType.FIR,
                     DocType.POST_MORTEM],
    SchemeId.RUPAY: [DocType.DEATH_CERTIFICATE, DocType.PASSBOOK, DocType.FIR,
                     DocType.POST_MORTEM],
}  # fmt: skip


class TransientAgentError(RuntimeError):
    """Raised by a flaky stub; Temporal retries it."""


@dataclass
class Scenario:
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


def _doc(case_id: str, doc_id: str, doc_type: DocType, fields: dict[str, str]) -> ExtractedDocument:
    return ExtractedDocument(
        doc_id=doc_id, case_id=case_id, doc_type=doc_type,
        fields=[ExtractedField(name=k, value=v, confidence=0.97) for k, v in fields.items()],
        masked_copy_ref=f"stub/{doc_id}.masked.png", sha256="0" * 64,
    )  # fmt: skip


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

    # conversation agent
    def interview(self, case_id: str) -> CaseFacts:
        self._maybe_fail("interview")
        return CaseFacts(
            case_id=case_id,
            deceased=Deceased(
                name_token="tok_stub_deceased", date_of_death=date(2026, 9, 14),
                place_of_death="Mysuru, Karnataka", event_type=EventType.ACCIDENT, age_at_death=41,
            ),
            claimants=[Claimant(
                claimant_id=f"{case_id}-c1", name_token="tok_stub_spouse",
                relationship=Relationship.SPOUSE, is_nominee=True,
                bank_account_token="tok_stub_spouse_acct",
            )],
            assets=[
                Asset(asset_id=f"{case_id}-a1", type=AssetType.BANK_ACCOUNT,
                      institution="State Bank of India", identifier_token="tok_stub_acct",
                      source="family_statement"),
                Asset(asset_id=f"{case_id}-a2", type=AssetType.DEBIT_CARD,
                      institution="State Bank of India", identifier_token="tok_stub_card",
                      card_variant="rupay_classic", last_txn_date=date(2026, 9, 10),
                      source=f"{case_id}-d1"),
            ],
        )  # fmt: skip

    # document agent
    def extract_documents(self, case_id: str) -> list[ExtractedDocument]:
        self._maybe_fail("extract_documents")
        return [
            _doc(case_id, f"{case_id}-d1", DocType.PASSBOOK, {
                "debit_2025_05_25": "PMSBY PREM 20", "debit_2025_06_01": "PMJJBY PREM 436",
                "last_card_txn": "2026-09-10"}),
            _doc(case_id, f"{case_id}-d2", DocType.DEATH_CERTIFICATE, {
                "date_of_death": "2026-09-14", "place_of_death": "Mysuru"}),
            _doc(case_id, f"{case_id}-d3", DocType.FIR, {"cause": "road accident"}),
            _doc(case_id, f"{case_id}-d4", DocType.POST_MORTEM, {"cause": "head injury"}),
        ]  # fmt: skip

    # discovery agent
    def discover(self, facts: CaseFacts, docs: list[ExtractedDocument]) -> list[Entitlement]:
        self._maybe_fail("discover")
        c, passbook = facts.case_id, docs[0].doc_id
        found = [
            (SchemeId.PMJJBY, f"{c}-a1", "debit_2025_06_01", "PMJJBY PREM 436"),
            (SchemeId.PMSBY, f"{c}-a1", "debit_2025_05_25", "PMSBY PREM 20"),
            (SchemeId.RUPAY, f"{c}-a2", "last_card_txn", "2026-09-10"),
        ]
        return [
            Entitlement(
                entitlement_id=f"{c}-e-{scheme.value}", case_id=c, scheme_id=scheme,
                asset_id=asset, evidence=[Evidence(doc_id=passbook, field=f, snippet=snip)],
                strength=Strength.POSSIBLE if scheme in self.s.weak_evidence else Strength.STRONG,
                expected_payout_inr=PAYOUT[scheme],
            )
            for scheme, asset, f, snip in found
        ]  # fmt: skip

    # rules engine (deterministic service; real one in M3)
    def check_eligibility(self, ent: Entitlement, known_facts: dict[str, str]) -> RuleResult:
        self._maybe_fail("check_eligibility")
        status = self.s.rule_status.get(ent.scheme_id, RuleStatus.ELIGIBLE)
        missing = [f for f in self.s.missing_facts.get(ent.scheme_id, []) if f not in known_facts]
        if status is RuleStatus.UNKNOWN and not missing:
            status = RuleStatus.ELIGIBLE
        explain = {
            RuleStatus.ELIGIBLE: f"{ent.scheme_id.value.upper()} cover was active on that date.",
            RuleStatus.NOT_ELIGIBLE: "The cover had lapsed before the date of death.",
            RuleStatus.UNKNOWN: f"We still need: {', '.join(missing)}.",
        }[status]  # fmt: skip
        return RuleResult(
            entitlement_id=ent.entitlement_id,
            status=status,
            rule_ids=[RULES[ent.scheme_id]],
            rule_version="stub-2025-26",
            explanation_en=explain,
            missing_facts=missing if status is RuleStatus.UNKNOWN else [],
        )

    # verifier agent
    def verify_entitlements(
        self, ents: list[Entitlement], results: list[RuleResult]
    ) -> VerifierVerdict:
        self._maybe_fail("verify_entitlements")
        findings = [
            Finding(severity=Severity.WARNING, target=f"entitlement:{e.entitlement_id}",
                    reason="Only weak evidence for this cover; a reviewer should confirm.")
            for e in ents if e.strength is Strength.POSSIBLE
        ]  # fmt: skip
        return VerifierVerdict(
            subject_id=f"{ents[0].case_id}:discovery", passed=True, findings=findings
        )

    def brief(self, ents: list[Entitlement], results: list[RuleResult]) -> str:
        by_id = {r.entitlement_id: r for r in results}
        lines = []
        for e in ents:
            r = by_id[e.entitlement_id]
            amount = f"about Rs {e.expected_payout_inr:,}" if e.expected_payout_inr else ""
            lines.append(
                f"{e.scheme_id.value.upper()}: {r.status.value} {amount}. {r.explanation_en}"
            )
        return "\n".join(lines)

    def missing_documents(self, claim_id: str, scheme: SchemeId) -> list[DocType]:
        self._maybe_fail("missing_documents")
        missing = self.s.missing_docs.get(scheme, [])
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
            cover_letter="To the Branch Manager, State Bank of India, Mysuru ..."
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
        return "paid", self.s.paid_amount.get(scheme, PAYOUT[scheme])
