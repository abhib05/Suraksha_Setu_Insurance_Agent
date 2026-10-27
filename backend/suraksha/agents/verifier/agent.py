"""Verifier (read-only): deterministic checks decide pass/fail; the model adds a second
opinion.

The checks that must never be skipped (every field traced and matching its source, dates
agreeing, required attachments present, eligible rule result, correct amount, a nominee)
are code, and any failure is a blocker. Muse Wonder then reviews the packet as a careful
bank officer would; its findings are shown to the reviewer as warnings (capped, so a model
mistake can never block a correct packet or loop a claim). If the model is unavailable,
the verdict says so instead of passing silently.
"""

import json
import logging
import re
from collections.abc import Callable
from datetime import date

from pydantic import BaseModel

from suraksha.contracts import (
    CaseFacts,
    ClaimPacket,
    DocType,
    Entitlement,
    ExtractedDocument,
    Finding,
    RuleResult,
    RuleStatus,
    Severity,
    Strength,
    VerifierVerdict,
)
from suraksha.services.registry import load_scheme, required_documents
from suraksha.services.rules.matchers import transactions
from suraksha.tools import llm
from suraksha.tools.prompts import load_prompt

log = logging.getLogger("suraksha.verifier")

# Fields whose value is allowed to differ in wording from the source (rewritten in English).
REWORDED = {"accident_description"}


def _norm(v: str | None) -> str:
    return " ".join((v or "").upper().replace(",", "").split())


def _blocker(target: str, reason: str) -> Finding:
    return Finding(severity=Severity.BLOCKER, target=target, reason=reason)


def deterministic_findings(
    packet: ClaimPacket,
    ent: Entitlement,
    facts: CaseFacts,
    docs: list[ExtractedDocument],
    rule_result: RuleResult,
) -> list[Finding]:
    found: list[Finding] = []
    by_id = {d.doc_id: d for d in docs}
    txns = transactions(docs)

    # 1. Every field filled, traced, and matching the document it cites.
    for form in packet.forms:
        for f in form.fields:
            target = f"{form.template_id}.{f.name}"
            if f.value is None:
                found.append(_blocker(target, "Required field is empty; no source was found."))
                continue
            if f.source_ref is None:
                found.append(_blocker(target, "Value has no source."))
                continue
            if f.source_ref.kind != "document_field":
                continue
            doc_id, _, field = f.source_ref.ref.partition("#")
            doc = by_id.get(doc_id)
            if doc is None:
                found.append(_blocker(target, f"Cites {doc_id}, which is not in the case."))
            elif field == "txn":
                m = re.match(r"(\d{4}-\d{2}-\d{2})(?: Rs (\d+))?", f.value)
                ok = m and any(t.doc_id == doc_id and t.on.isoformat() == m.group(1)
                               and (not m.group(2) or round(t.amount) == int(m.group(2)))
                               for t in txns)  # fmt: skip
                if not ok:
                    found.append(_blocker(target, f"'{f.value}' is not a line in {doc_id}."))
            elif f.name not in REWORDED:
                src = doc.field(field)
                src_val = src.value if src else None
                if field == "account_number_last4":
                    match = src_val is not None and f.value.endswith(src_val[-4:])
                else:
                    match = _norm(src_val) == _norm(f.value)
                if not match:
                    found.append(_blocker(target, f"Value '{f.value}' differs from {doc_id} "
                                                  f"{field} '{src_val}'."))  # fmt: skip

    # 2. The date of death agrees everywhere it appears.
    dates = {}
    for form in packet.forms:
        for f in form.fields:
            if f.name == "date_of_death" and f.value:
                dates[f"{form.template_id}"] = f.value
    for d in docs:
        if d.doc_type is DocType.DEATH_CERTIFICATE and (f := d.field("date_of_death")) and f.value:
            dates["death certificate"] = f.value
    if facts.deceased.date_of_death:
        dates["family statement"] = facts.deceased.date_of_death.isoformat()
    if len(set(dates.values())) > 1:
        found.append(_blocker("date_of_death", "Date of death does not match: " + ", ".join(
            f"{k} {v}" for k, v in dates.items())))  # fmt: skip

    # 3. Every document the scheme requires is attached, and attachments are real.
    attached = {by_id[a.doc_id].doc_type for a in packet.attachments if a.doc_id in by_id}
    for a in packet.attachments:
        if a.doc_id not in by_id:
            found.append(_blocker("attachments", f"Attachment {a.doc_id} is not in the case."))
    for t in required_documents(packet.scheme_id):
        if t not in attached:
            found.append(_blocker("attachments", f"Required document missing: {t.value}."))

    # 4. Eligibility, amount and nominee.
    if rule_result.status is not RuleStatus.ELIGIBLE:
        found.append(_blocker("packet", f"Eligibility is {rule_result.status.value} "
                                        f"({', '.join(rule_result.rule_ids)})."))  # fmt: skip
    max_payout = max(
        [load_scheme(packet.scheme_id)["payout_inr"]]
        + [
            t["payout_inr"]
            for t in load_scheme(packet.scheme_id).get("payout_by_card_issue_date", [])
        ]
    )
    for form in packet.forms:
        for f in form.fields:
            if (
                f.name == "claim_amount_inr"
                and f.value
                and (int(f.value) != ent.expected_payout_inr or int(f.value) > max_payout)
            ):
                found.append(
                    _blocker(
                        f"{form.template_id}.claim_amount_inr",
                        f"Amount {f.value} is not the scheme payout.",
                    )
                )
    if not any(f.name == "nominee_name" and f.value for form in packet.forms for f in form.fields):
        found.append(_blocker("nominee_name", "No nominee or claimant is named."))
    return found


class ModelFindings(BaseModel):
    findings: list[Finding]


def model_findings(
    packet: ClaimPacket,
    facts: CaseFacts,
    docs: list[ExtractedDocument],
    rule_result: RuleResult,
    complete_json: Callable | None = None,
) -> list[Finding]:
    prompt = load_prompt("verifier")
    complete_json = complete_json or llm.complete_json
    material = {
        "packet": packet.model_dump(mode="json"),
        "family_facts": facts.model_dump(mode="json"),
        "documents": [{"doc_id": d.doc_id, "doc_type": d.doc_type.value,
                       "fields": [(f.name, f.value) for f in d.fields]} for d in docs],
        "eligibility": rule_result.model_dump(mode="json"),
        "today": date.today().isoformat(),
    }  # fmt: skip
    out, _ = complete_json(
        "verifier",
        [{"role": "system", "content": prompt.text},
         {"role": "user", "content": json.dumps(material, ensure_ascii=False, default=str)}],
        ModelFindings,
    )  # fmt: skip
    capped = []
    for f in out.findings:
        sev = Severity.WARNING if f.severity is Severity.BLOCKER else f.severity
        capped.append(f.model_copy(update={"severity": sev, "reason": f"[model] {f.reason}"}))
    return capped


def verify_packet(
    packet: ClaimPacket,
    ent: Entitlement,
    facts: CaseFacts,
    docs: list[ExtractedDocument],
    rule_result: RuleResult,
    *,
    use_model: bool = True,
    complete_json: Callable | None = None,
) -> VerifierVerdict:
    findings = deterministic_findings(packet, ent, facts, docs, rule_result)
    if use_model:
        try:
            findings += model_findings(packet, facts, docs, rule_result, complete_json)
        except Exception as e:
            log.info("model review unavailable (%s)", type(e).__name__)
            findings.append(Finding(severity=Severity.WARNING, target="packet",
                                    reason="AI review did not run (model unavailable); "
                                           "deterministic checks only."))  # fmt: skip
    passed = not any(f.severity is Severity.BLOCKER for f in findings)
    return VerifierVerdict(subject_id=packet.idempotency_key, passed=passed, findings=findings)


def verify_entitlements(
    case_id: str, ents: list[Entitlement], docs: list[ExtractedDocument]
) -> VerifierVerdict:
    """Discovery check: every cover cites evidence that exists; weak evidence is flagged."""
    by_id = {d.doc_id for d in docs}
    findings = []
    for e in ents:
        target = f"entitlement:{e.entitlement_id}"
        if e.strength is Strength.POSSIBLE:
            findings.append(Finding(severity=Severity.WARNING, target=target,
                                    reason="Only weak evidence for this cover; a reviewer "
                                           "should confirm."))  # fmt: skip
        doc_evidence = [ev for ev in e.evidence if ev.doc_id in by_id]
        if not doc_evidence:
            findings.append(
                Finding(
                    severity=Severity.WARNING,
                    target=target,
                    reason="No uploaded document supports this cover yet.",
                )
            )
    return VerifierVerdict(subject_id=f"{case_id}:discovery", passed=True, findings=findings)
