"""Filing agent: entitlement + facts + documents -> one ClaimPacket per claim.

Every form value is looked up from a named source (see sources.py), so the packet can be
traced field by field. The model's only job here is to turn the FIR's accident
description into plain English for the bank declaration; the result keeps the FIR as its
source, and if the model is unavailable the FIR text is used as is.
"""

import logging
from collections.abc import Callable

from pydantic import BaseModel

from suraksha.agents.filing.forms import ATTACHMENT_LABELS, FORMS, SIGN_OFF
from suraksha.agents.filing.sources import SourceIndex
from suraksha.contracts import (
    Attachment,
    CaseFacts,
    ChecklistItem,
    ClaimPacket,
    Entitlement,
    ExtractedDocument,
    FilledForm,
    FormField,
)
from suraksha.services.registry import load_scheme, required_documents
from suraksha.tools import llm
from suraksha.tools.prompts import load_prompt

log = logging.getLogger("suraksha.filing")


class AccidentText(BaseModel):
    description_en: str
    translated: bool


def english_description(raw: str, complete_json: Callable | None = None) -> str:
    prompt = load_prompt("filing")
    complete_json = complete_json or llm.complete_json
    try:
        out, _ = complete_json(
            "filing",
            [{"role": "system", "content": prompt.text},
             {"role": "user", "content": f"FIR description:\n<<<\n{raw}\n>>>"}],
            AccidentText,
        )  # fmt: skip
        return out.description_en.strip() or raw
    except Exception as e:  # model unavailable: the FIR's own words are still correct
        log.info("accident description left as written (%s)", type(e).__name__)
        return raw


def build_packet(
    claim_id: str,
    ent: Entitlement,
    facts: CaseFacts,
    docs: list[ExtractedDocument],
    version: int,
    reviewer_comment: str | None = None,
    *,
    use_model: bool = True,
    complete_json: Callable | None = None,
) -> ClaimPacket:
    index = SourceIndex(facts, docs, ent)
    described: dict[str, str] = {}
    forms = []
    for template_id, spec in FORMS[ent.scheme_id].items():
        fields = []
        for f in spec:
            hit = index.get(f.key)
            if hit is None:
                if f.required:  # left empty on purpose: the verifier blocks the packet
                    fields.append(FormField(name=f.key, value=None, source_ref=None))
                continue
            value = hit.value
            if f.key == "accident_description" and use_model:
                if value not in described:
                    described[value] = english_description(value, complete_json)
                value = described[value]
            fields.append(FormField(name=f.key, value=value, source_ref=hit.ref))
        forms.append(FilledForm(template_id=template_id, fields=fields))

    held = {d.doc_type: d for d in docs}
    attachments, checklist = [], []
    for doc_type in required_documents(ent.scheme_id):
        label = ATTACHMENT_LABELS.get(doc_type, doc_type.value)
        if doc_type in held:
            attachments.append(Attachment(doc_id=held[doc_type].doc_id, label=label,
                                          order=len(attachments) + 1))  # fmt: skip
        else:
            checklist.append(ChecklistItem(item=f"Missing: {label}", done=False))
    checklist += [ChecklistItem(item=item, done=False) for item in SIGN_OFF]

    return ClaimPacket(
        claim_id=claim_id, scheme_id=ent.scheme_id, version=version, forms=forms,
        attachments=attachments, checklist=checklist,
        cover_letter=cover_letter(ent, index, attachments, reviewer_comment),
    )  # fmt: skip


def cover_letter(
    ent: Entitlement, index: SourceIndex, attachments: list[Attachment], comment: str | None
) -> str:
    reg = load_scheme(ent.scheme_id)
    bank = index.get("bank_name")
    dod = index.get("date_of_death")
    amount = ent.expected_payout_inr
    lines = [
        f"To the Branch Manager, {bank.value if bank else '[bank]'}",
        "",
        f"Subject: Claim under {reg['name']} ({ent.scheme_id.value.upper()})",
        "",
        "Respected Sir/Madam,",
        "",
        f"I write to claim the benefit under {reg['name']} following the death of the "
        f"account holder on {dod.value if dod else '[date of death]'}. "
        + (f"The sum assured under the scheme is Rs {amount:,}. " if amount else "")
        + "The completed claim form and the following documents are enclosed:",
        "",
        *[f"{a.order}. {a.label}" for a in attachments],
        "",
        "I request you to process the claim and credit the amount to the nominee's account.",
        "",
        "Yours faithfully,",
        "(Nominee: signature / thumb impression)",
    ]
    if comment:
        lines += ["", f"(Revised: {comment})"]
    return "\n".join(lines)
