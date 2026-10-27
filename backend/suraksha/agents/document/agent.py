"""Document agent: masked document image -> ExtractedDocument.

The model only ever sees the Aadhaar-masked working copy. Its output is checked here, not
trusted: field names are limited to the known set, low-confidence fields are flagged for a
reviewer, and any instruction found inside the document becomes a tamper signal.
"""

import base64
import hashlib
from collections.abc import Callable

from pydantic import BaseModel, Field

from suraksha.contracts import DocType, ExtractedDocument, ExtractedField, QualityFlag
from suraksha.services.docstore.aadhaar import mask_text
from suraksha.tools import llm
from suraksha.tools.prompts import load_prompt

LOW_CONFIDENCE = 0.7

KNOWN_FIELDS = {
    DocType.PASSBOOK: {"account_holder", "account_number_last4", "institution", "branch", "txn"},
    DocType.BANK_STATEMENT: {"account_holder", "account_number_last4", "institution", "branch",
                             "txn"},
    DocType.DEATH_CERTIFICATE: {"name", "date_of_death", "place_of_death", "cause",
                                "registration_number"},
    DocType.FIR: {"police_station", "date_of_incident", "cause", "fir_number"},
    DocType.POST_MORTEM: {"hospital", "date_of_examination", "cause"},
    DocType.ID_PROOF: {"name", "date_of_birth", "aadhaar_masked"},
}  # fmt: skip


class ReadField(BaseModel):
    name: str
    value: str | None
    confidence: float = Field(ge=0, le=1)


class DocumentReading(BaseModel):
    """What the model returns (validated against this schema)."""

    doc_type: DocType
    fields: list[ReadField]
    quality_flags: list[QualityFlag] = []
    embedded_instructions: list[str] = []


class DocumentResult(BaseModel):
    document: ExtractedDocument
    needs_review: list[str]  # field names a human should confirm
    model: str
    prompt_version: str


def read_document(
    case_id: str,
    doc_id: str,
    masked_image: bytes,
    *,
    mime_type: str = "image/png",
    original_sha256: str | None = None,
    masked_ref: str = "",
    complete_json: Callable | None = None,
) -> DocumentResult:
    prompt = load_prompt("document")
    complete_json = complete_json or llm.complete_json  # looked up per call (tests swap it)
    data_url = f"data:{mime_type};base64,{base64.b64encode(masked_image).decode()}"
    messages = [
        {"role": "system", "content": prompt.text},
        {"role": "user", "content": [
            {"type": "text", "text": "Read this document and return the JSON described."},
            {"type": "image_url", "image_url": {"url": data_url}},
        ]},
    ]  # fmt: skip
    reading, result = complete_json("document", messages, DocumentReading)
    return DocumentResult(
        document=to_extracted(case_id, doc_id, reading, masked_ref,
                              original_sha256 or hashlib.sha256(masked_image).hexdigest()),
        needs_review=[f.name for f in reading.fields if f.confidence < LOW_CONFIDENCE],
        model=result.model, prompt_version=prompt.version,
    )  # fmt: skip


def to_extracted(
    case_id: str, doc_id: str, reading: DocumentReading, masked_ref: str, sha256: str
) -> ExtractedDocument:
    allowed = KNOWN_FIELDS.get(reading.doc_type)
    fields, signals = [], [f"embedded_instruction: {t}" for t in reading.embedded_instructions]
    for f in reading.fields:
        if allowed is not None and f.name not in allowed:
            signals.append(f"unexpected_field: {f.name}")
            continue
        value = f.value
        if value is not None:
            # Belt and braces: a full Aadhaar number never leaves this function unmasked.
            value, n = mask_text(value)
            if n:
                signals.append(f"unmasked_aadhaar_in_{f.name}")
        fields.append(ExtractedField(name=f.name, value=value, confidence=f.confidence))
    return ExtractedDocument(
        doc_id=doc_id, case_id=case_id, doc_type=reading.doc_type, fields=fields,
        quality_flags=reading.quality_flags, tamper_signals=signals,
        masked_copy_ref=masked_ref or f"{doc_id}.masked", sha256=sha256,
    )  # fmt: skip
