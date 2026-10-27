from pydantic import Field, field_validator

from suraksha.contracts.common import Contract, SchemeId


class SourceRef(Contract):
    """Where a packet value came from (FR-PRE-04): a document field or a family statement."""

    kind: str = Field(pattern=r"^(document_field|family_statement|registry)$")
    ref: str  # "<doc_id>#<field>", a statement id, or "<scheme>.<registry key>"


class FormField(Contract):
    name: str
    value: str | None
    source_ref: SourceRef | None  # None is allowed in a draft; the verifier fails it


class FilledForm(Contract):
    template_id: str
    fields: list[FormField]


class Attachment(Contract):
    doc_id: str
    label: str  # e.g. "Death certificate (attested copy)"
    order: int = Field(ge=1)


class ChecklistItem(Contract):
    item: str
    done: bool


class ClaimPacket(Contract):
    """Produced by the filing agent: everything one claim desk needs, in order."""

    claim_id: str
    scheme_id: SchemeId
    version: int = Field(ge=1)
    forms: list[FilledForm]
    attachments: list[Attachment] = []
    cover_letter: str
    checklist: list[ChecklistItem] = []

    @field_validator("attachments")
    @classmethod
    def _unique_order(cls, v: list[Attachment]) -> list[Attachment]:
        orders = [a.order for a in v]
        if len(orders) != len(set(orders)):
            raise ValueError("attachment order values must be unique")
        return v

    def untraced_fields(self) -> list[str]:
        """Fields with a value but no source; the verifier fails a packet that has any."""
        return [
            f"{form.template_id}.{f.name}"
            for form in self.forms
            for f in form.fields
            if f.value is not None and f.source_ref is None
        ]

    @property
    def idempotency_key(self) -> str:
        return f"{self.claim_id}:v{self.version}"
