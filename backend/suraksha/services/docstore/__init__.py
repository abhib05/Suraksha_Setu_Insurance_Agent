"""Document store: keeps the encrypted original and an Aadhaar-masked working copy.

Agents and the Meta API only ever get `working_copy()`. Originals stay encrypted on disk and
are opened only for filing, through the vault key.
"""

import hashlib
from pathlib import Path

from sqlalchemy.orm import Session

from suraksha import audit
from suraksha.config import get_settings
from suraksha.db.base import new_id
from suraksha.db.models import Document
from suraksha.services import consent, vault
from suraksha.services.docstore.aadhaar import mask_image

IMAGE_TYPES = {"image/jpeg", "image/png", "image/webp"}
MAX_BYTES = 15 * 1024 * 1024


class DocumentRejectedError(ValueError):
    pass


class NotMaskedError(PermissionError):
    """The document has no safe working copy (e.g. a PDF before PDF masking exists)."""


def save(
    session: Session,
    case_id: str,
    filename: str,
    data: bytes,
    mime_type: str,
    *,
    root: Path | None = None,
    actor: str = "user:family",
) -> Document:
    consent.require(session, case_id)
    if not data:
        raise DocumentRejectedError("empty file")
    if len(data) > MAX_BYTES:
        raise DocumentRejectedError("file larger than 15 MB")

    root = root or get_settings().docstore_dir
    doc_id = new_id("doc")
    case_dir = root / case_id
    case_dir.mkdir(parents=True, exist_ok=True)

    original_path = case_dir / f"{doc_id}.orig.enc"
    original_path.write_bytes(vault.encrypt_bytes(data))

    masked_path: Path | None = None
    if mime_type in IMAGE_TYPES:
        masked, found = mask_image(data)
        masked_path = case_dir / f"{doc_id}.masked.png"
        masked_path.write_bytes(masked)
        status = "masked" if found else "none_found"
    else:
        found, status = 0, "unsupported"

    doc = Document(
        id=doc_id,
        case_id=case_id,
        filename=Path(filename).name[:255],
        mime_type=mime_type,
        size_bytes=len(data),
        sha256=hashlib.sha256(data).hexdigest(),
        original_path=str(original_path),
        masked_path=str(masked_path) if masked_path else None,
        masking_status=status,
        aadhaar_masked=found,
    )
    session.add(doc)
    session.flush()
    audit.append(
        session,
        actor=actor,
        action="document.stored",
        case_id=case_id,
        input=data,
        detail={
            "doc_id": doc_id,
            "mime_type": mime_type,
            "masking_status": status,
            "aadhaar_masked": found,
        },
    )
    return doc


def working_copy(doc: Document) -> bytes:
    """The only bytes an agent may read or send to a model."""
    if doc.masking_status not in ("masked", "none_found") or not doc.masked_path:
        raise NotMaskedError(f"{doc.id} has no masked working copy ({doc.masking_status})")
    return Path(doc.masked_path).read_bytes()


def original(session: Session, doc: Document, *, actor: str, purpose: str) -> bytes:
    """Decrypt the original for filing. Audited like a vault reveal."""
    audit.append(
        session,
        actor=actor,
        action="document.original_opened",
        case_id=doc.case_id,
        detail={"doc_id": doc.id, "purpose": purpose},
    )
    data = vault.decrypt_bytes(Path(doc.original_path).read_bytes())
    if hashlib.sha256(data).hexdigest() != doc.sha256:
        raise ValueError(f"{doc.id}: original does not match its recorded hash")
    return data
