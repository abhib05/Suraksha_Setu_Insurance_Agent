"""Intake service: the encrypted chat log, the interview draft, and draft -> CaseFacts.

Names the family types are tokenised into the vault the moment they are merged, so the
draft (and everything built from it) only ever holds `tok_...` values.
"""

import contextlib
from datetime import date
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from suraksha.agents.conversation.agent import IntakeUpdates
from suraksha.contracts import (
    Asset,
    AssetType,
    CaseFacts,
    Claimant,
    Deceased,
    EventType,
    Relationship,
)
from suraksha.db import models as m
from suraksha.services import vault

NAME_FIELDS = {"speaker_name": "speaker_name_token", "deceased_name": "deceased_name_token",
               "nominee_name": "nominee_name_token"}  # fmt: skip


def get_intake(session: Session, case_id: str) -> m.Intake:
    row = session.get(m.Intake, case_id)
    if row is None:
        # The console's first GET and POST of the chat can both get here; the loser of the
        # insert reads the winner's row instead of failing.
        try:
            with session.begin_nested():
                row = m.Intake(case_id=case_id, draft={})
                session.add(row)
        except IntegrityError:
            row = session.get(m.Intake, case_id, populate_existing=True)
    return row


def add_message(session: Session, case_id: str, role: str, text: str) -> None:
    session.add(m.ChatMessage(case_id=case_id, role=role,
                              ciphertext=vault.encrypt_bytes(text.encode())))  # fmt: skip
    session.flush()


def history(session: Session, case_id: str) -> list[dict[str, str]]:
    rows = session.scalars(
        select(m.ChatMessage).where(m.ChatMessage.case_id == case_id).order_by(m.ChatMessage.id)
    )
    return [{"role": r.role, "content": vault.decrypt_bytes(r.ciphertext).decode()} for r in rows]


def merge(session: Session, intake: m.Intake, updates: IntakeUpdates) -> list[str]:
    """Apply non-null updates; returns the draft keys that changed (for the audit log)."""
    draft = dict(intake.draft or {})
    changed = []
    for key, value in updates.model_dump(exclude_none=True, mode="json").items():
        if key in NAME_FIELDS:
            token = vault.tokenize(session, vault.SecretKind.NAME, str(value))
            key, value = NAME_FIELDS[key], token
        if draft.get(key) != value:
            draft[key] = value
            changed.append(key)
    # "I am the nominee": the agent only sees "known" for names, so it cannot copy the
    # speaker's name across itself.
    nominee_rel = draft.get("nominee_relationship")
    speaker_is_nominee = nominee_rel and nominee_rel == draft.get("speaker_relationship")
    if speaker_is_nominee and draft.get("speaker_name_token") and "nominee_name_token" not in draft:
        draft["nominee_name_token"] = draft["speaker_name_token"]
        changed.append("nominee_name_token")
    intake.draft = draft
    return changed


def known_for_prompt(draft: dict[str, Any]) -> dict[str, Any]:
    """What the agent may see of the draft: tokens are replaced by 'known'."""
    return {k.removesuffix("_token"): ("known" if k.endswith("_token") else v)
            for k, v in draft.items()}  # fmt: skip


_RELATIONSHIP = {
    "wife": Relationship.SPOUSE, "husband": Relationship.SPOUSE, "spouse": Relationship.SPOUSE,
    "son": Relationship.CHILD, "daughter": Relationship.CHILD, "child": Relationship.CHILD,
    "father": Relationship.PARENT, "mother": Relationship.PARENT, "parent": Relationship.PARENT,
    "brother": Relationship.SIBLING, "sister": Relationship.SIBLING,
    # Hindi and Kannada, in case a value comes back in the family's script (prompt asks
    # for English; seen live before that rule).
    "पत्नी": Relationship.SPOUSE, "पति": Relationship.SPOUSE, "बेटा": Relationship.CHILD,
    "बेटी": Relationship.CHILD, "पिता": Relationship.PARENT, "माता": Relationship.PARENT,
    "माँ": Relationship.PARENT, "भाई": Relationship.SIBLING, "बहन": Relationship.SIBLING,
    "ಪತ್ನಿ": Relationship.SPOUSE, "ಹೆಂಡತಿ": Relationship.SPOUSE, "ಪತಿ": Relationship.SPOUSE,
    "ಗಂಡ": Relationship.SPOUSE, "ಮಗ": Relationship.CHILD, "ಮಗಳು": Relationship.CHILD,
    "ತಂದೆ": Relationship.PARENT, "ತಾಯಿ": Relationship.PARENT, "ಅಣ್ಣ": Relationship.SIBLING,
    "ತಮ್ಮ": Relationship.SIBLING, "ಅಕ್ಕ": Relationship.SIBLING, "ತಂಗಿ": Relationship.SIBLING,
}  # fmt: skip


def _relationship(text: str | None) -> Relationship:
    return _RELATIONSHIP.get((text or "").strip().lower(), Relationship.OTHER)


def to_case_facts(case_id: str, draft: dict[str, Any]) -> CaseFacts:
    """The interview as a CaseFacts contract (documents enrich it later)."""
    dod = draft.get("date_of_death")
    deceased = Deceased(
        name_token=draft.get("deceased_name_token") or "tok_unknown_deceased",
        date_of_death=date.fromisoformat(dod) if dod else None,
        place_of_death=draft.get("place_of_death"),
        event_type=EventType(draft.get("event_type") or "unknown"),
        age_at_death=draft.get("age_at_death"),
    )
    claimants = []
    if draft.get("nominee_name_token") or draft.get("speaker_name_token"):
        same = not draft.get("nominee_name_token")
        claimants.append(Claimant(
            claimant_id=f"{case_id}-c1",
            name_token=draft.get("nominee_name_token") or draft["speaker_name_token"],
            relationship=_relationship(draft.get("nominee_relationship")
                                       or (draft.get("speaker_relationship") if same else None)),
            is_nominee=None if same else True,
        ))  # fmt: skip
    assets = []
    if draft.get("bank_name"):
        assets.append(
            Asset(
                asset_id=f"{case_id}-a1",
                type=AssetType.BANK_ACCOUNT,
                institution=draft["bank_name"],
                source="family_statement",
            )
        )
    if draft.get("has_debit_card") and draft.get("card_kind") not in (None, "none"):
        kind = draft["card_kind"]
        last, issued = draft.get("card_last_used"), draft.get("card_issue_date")
        assets.append(Asset(
            asset_id=f"{case_id}-a2", type=AssetType.DEBIT_CARD,
            institution=draft.get("bank_name"),
            card_variant=kind if kind != "unknown" else None,
            last_txn_date=date.fromisoformat(last) if last else None,
            card_issue_date=date.fromisoformat(issued) if issued else None,
            source="family_statement",
        ))  # fmt: skip
    return CaseFacts(case_id=case_id, deceased=deceased, claimants=claimants, assets=assets)


def enrich(facts: CaseFacts, docs: list) -> CaseFacts:
    """Fill gaps in the interview from what the documents show (never overwrite the family's
    answers; the verifier compares the two in M5)."""
    from suraksha.contracts import DocType
    from suraksha.services.rules.matchers import CARD_CHANNEL, transactions

    def field(doc_type, name):
        for d in docs:
            if d.doc_type is doc_type:
                f = d.field(name)
                if f and f.value:
                    return f.value
        return None

    deceased = facts.deceased
    updates = {}
    if deceased.date_of_death is None and (
        dod := field(DocType.DEATH_CERTIFICATE, "date_of_death")
    ):
        with contextlib.suppress(ValueError):  # an unreadable date stays a question
            updates["date_of_death"] = date.fromisoformat(dod)
    if not deceased.place_of_death and (
        place := field(DocType.DEATH_CERTIFICATE, "place_of_death")
    ):
        updates["place_of_death"] = place
    if updates:
        deceased = deceased.model_copy(update=updates)

    assets = list(facts.assets)
    types = {a.type for a in assets}
    passbook = next((d for d in docs if d.doc_type in (DocType.PASSBOOK, DocType.BANK_STATEMENT)),
                    None)  # fmt: skip
    if AssetType.BANK_ACCOUNT not in types and passbook:
        inst = field(passbook.doc_type, "institution")
        assets.append(
            Asset(
                asset_id=f"{facts.case_id}-a1",
                type=AssetType.BANK_ACCOUNT,
                institution=inst,
                source=passbook.doc_id,
            )
        )
    if AssetType.DEBIT_CARD not in types and passbook:
        rupay = [
            t
            for t in transactions(docs)
            if "RUPAY" in t.description.upper() and CARD_CHANNEL.search(t.description)
        ]
        if rupay:
            assets.append(
                Asset(
                    asset_id=f"{facts.case_id}-a2",
                    type=AssetType.DEBIT_CARD,
                    card_variant="rupay",
                    source=passbook.doc_id,
                )
            )
    return facts.model_copy(update={"deceased": deceased, "assets": assets})
