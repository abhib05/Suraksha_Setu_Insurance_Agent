"""Where each claim-form value comes from.

`SourceIndex` answers "what is the date of death, and how do we know?" with a value and a
SourceRef. Documents win over what the family said (they are what the insurer checks);
the family's statement fills gaps; the registry supplies scheme facts like the payout.
"""

from dataclasses import dataclass

from suraksha.contracts import (
    AssetType,
    CaseFacts,
    DocType,
    Entitlement,
    ExtractedDocument,
    SchemeId,
    SourceRef,
)
from suraksha.services.rules.matchers import last_card_use, premium_debits, transactions


@dataclass(frozen=True)
class Sourced:
    value: str
    ref: SourceRef


def _doc(doc_id: str, field: str) -> SourceRef:
    return SourceRef(kind="document_field", ref=f"{doc_id}#{field}")


def _family(key: str) -> SourceRef:
    return SourceRef(kind="family_statement", ref=f"intake#{key}")


class SourceIndex:
    def __init__(self, facts: CaseFacts, docs: list[ExtractedDocument], ent: Entitlement):
        self.facts, self.docs, self.ent = facts, docs, ent
        self._txns = transactions(docs)

    def _from_docs(self, doc_types: tuple[DocType, ...], field: str) -> Sourced | None:
        for t in doc_types:
            for d in self.docs:
                if d.doc_type is t and (f := d.field(field)) and f.value:
                    return Sourced(f.value, _doc(d.doc_id, field))
        return None

    def get(self, key: str) -> Sourced | None:
        f, dec = self.facts, self.facts.deceased
        nominee = next((c for c in f.claimants if c.is_nominee), None) or (
            f.claimants[0] if f.claimants else None)  # fmt: skip
        bank = next((a for a in f.assets if a.type is AssetType.BANK_ACCOUNT), None)
        card = next((a for a in f.assets if a.type is AssetType.DEBIT_CARD), None)
        DC, FIR, PM = DocType.DEATH_CERTIFICATE, DocType.FIR, DocType.POST_MORTEM
        PB = (DocType.PASSBOOK, DocType.BANK_STATEMENT)

        match key:
            case "deceased_name":
                return self._from_docs((DC,), "name") or Sourced(
                    dec.name_token, _family("deceased_name")
                )
            case "date_of_death":
                return self._from_docs((DC,), "date_of_death") or (
                    Sourced(dec.date_of_death.isoformat(), _family("date_of_death"))
                    if dec.date_of_death
                    else None
                )
            case "place_of_death":
                return self._from_docs((DC,), "place_of_death") or (
                    Sourced(dec.place_of_death, _family("place_of_death"))
                    if dec.place_of_death
                    else None
                )
            case "cause_of_death":
                return self._from_docs((FIR, PM, DC), "cause") or (
                    Sourced(dec.event_type.value, _family("event_type"))
                    if dec.event_type.value != "unknown"
                    else None
                )
            case "accident_description":
                return self._from_docs((FIR,), "cause")
            case "fir_number":
                return self._from_docs((FIR,), "fir_number")
            case "police_station":
                return self._from_docs((FIR,), "police_station")
            case "post_mortem_hospital":
                return self._from_docs((PM,), "hospital")
            case "bank_name":
                return self._from_docs(PB, "institution") or (
                    Sourced(bank.institution, _family("bank_name"))
                    if bank and bank.institution
                    else None
                )
            case "account_number":
                hit = self._from_docs(PB, "account_number_last4")
                return Sourced(f"XXXX{hit.value[-4:]}", hit.ref) if hit else None
            case "nominee_name":
                return Sourced(nominee.name_token, _family("nominee_name")) if nominee else None
            case "nominee_relationship":
                return (
                    Sourced(nominee.relationship.value, _family("nominee_relationship"))
                    if nominee
                    else None
                )
            case "nominee_account":
                return (
                    Sourced(nominee.bank_account_token, _family("nominee_account"))
                    if nominee and nominee.bank_account_token
                    else None
                )
            case "premium_debit":
                hits = [
                    h
                    for h in premium_debits(self._txns, self.ent.scheme_id)
                    if dec.date_of_death is None or h.txn.on <= dec.date_of_death
                ]
                if not hits:
                    return None
                t = hits[-1].txn
                return Sourced(f"{t.on.isoformat()} Rs {round(t.amount)}", _doc(t.doc_id, "txn"))
            case "card_last_used":
                use = last_card_use(self._txns, dec.date_of_death)
                if use:
                    return Sourced(use.on.isoformat(), _doc(use.doc_id, "txn"))
                if card and card.last_txn_date:
                    return Sourced(card.last_txn_date.isoformat(), _family("card_last_used"))
                return None
            case "card_issue_date":
                return (
                    Sourced(card.card_issue_date.isoformat(), _family("card_issue_date"))
                    if card and card.card_issue_date
                    else None
                )
            case "claim_amount_inr":
                amount = self.ent.expected_payout_inr
                return (Sourced(str(amount), SourceRef(kind="registry",
                                                       ref=f"{self.ent.scheme_id.value}.payout"))
                        if amount else None)  # fmt: skip
            case "scheme":
                return Sourced(
                    self.ent.scheme_id.value.upper(),
                    SourceRef(kind="registry", ref=f"{self.ent.scheme_id.value}.name"),
                )
        raise KeyError(f"no source rule for form field {key!r}")  # pragma: no cover


SCHEME_IDS = tuple(SchemeId)
