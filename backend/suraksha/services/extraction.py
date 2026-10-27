"""Run the document agent on a stored document's masked copy and keep the result."""

from pathlib import Path

from sqlalchemy.orm import Session

from suraksha import audit
from suraksha.agents.document.agent import read_document
from suraksha.contracts import ExtractedDocument
from suraksha.db import models as m
from suraksha.db.base import new_id
from suraksha.services import docstore, vault

# Document fields that hold a person's name: stored only as vault tokens.
NAME_FIELDS = {"name", "account_holder"}


def tokenise_names(session: Session, doc: ExtractedDocument) -> ExtractedDocument:
    fields = [
        f.model_copy(update={"value": vault.tokenize(session, vault.SecretKind.NAME, f.value)})
        if f.name in NAME_FIELDS and f.value and not f.value.startswith("tok_") else f
        for f in doc.fields
    ]  # fmt: skip
    return doc.model_copy(update={"fields": fields})


def extract_and_store(session: Session, doc: m.Document) -> m.Document:
    """Raises docstore.NotMaskedError for documents with no safe working copy, and the
    model errors from tools.llm when the API is unavailable; the caller decides."""
    image = docstore.working_copy(doc)
    result = read_document(
        doc.case_id, doc.id, image, mime_type="image/png", original_sha256=doc.sha256,
        masked_ref=Path(doc.masked_path or "").name,
    )  # fmt: skip
    extracted = tokenise_names(session, result.document)
    doc.doc_type = extracted.doc_type.value
    doc.extraction = extracted.model_dump(mode="json")
    session.flush()
    audit.append(
        session, actor="agent:document", action="document.extracted", case_id=doc.case_id,
        model=result.model, prompt_version=result.prompt_version, input=doc.sha256,
        output=extracted,
        detail={"doc_id": doc.id, "doc_type": extracted.doc_type.value,
                "fields": len(extracted.fields), "needs_review": result.needs_review,
                "tamper_signals": len(extracted.tamper_signals)},
    )  # fmt: skip
    reasons = []
    if result.needs_review:
        reasons.append("low-confidence fields: " + ", ".join(sorted(set(result.needs_review))))
    if extracted.tamper_signals:
        reasons.append("possible tampering: " + "; ".join(extracted.tamper_signals))
    if extracted.quality_flags:
        reasons.append("photo quality: " + ", ".join(q.value for q in extracted.quality_flags))
    if reasons:
        task = m.Task(
            id=new_id("task"),
            case_id=doc.case_id,
            kind="document_review",
            assignee="reviewer",
            detail=f"{doc.filename}: " + " | ".join(reasons),
        )
        session.add(task)
        session.flush()
        audit.append(
            session,
            actor="service:extraction",
            action="task.created",
            case_id=doc.case_id,
            detail={"task_id": task.id, "kind": task.kind},
        )
    return doc
