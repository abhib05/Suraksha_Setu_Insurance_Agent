"""Claim form specs: which fields each scheme's form has, in order, and whether a packet can
go out without them. Field keys map to SourceIndex.get(). These mirror the content of the
official claim forms; the printed layout comes from templates/packet.html.j2."""

from dataclasses import dataclass

from suraksha.contracts import DocType, SchemeId


@dataclass(frozen=True)
class FormField:
    key: str
    label: str
    required: bool = True


COMMON_DEATH = [
    FormField("scheme", "Scheme"),
    FormField("deceased_name", "Name of the deceased member"),
    FormField("bank_name", "Bank (master policy holder)"),
    FormField("account_number", "Savings account of the deceased"),
    FormField("date_of_death", "Date of death"),
    FormField("place_of_death", "Place of death"),
    FormField("cause_of_death", "Cause of death"),
]
NOMINEE = [
    FormField("nominee_name", "Name of nominee / claimant"),
    FormField("nominee_relationship", "Relationship with the deceased"),
    FormField("nominee_account", "Nominee's bank account for payment", required=False),
    FormField("claim_amount_inr", "Amount claimed (Rs)"),
]
ACCIDENT = [
    FormField("accident_description", "Brief description of the accident (as per FIR)"),
    FormField("fir_number", "FIR number", required=False),
    FormField("police_station", "Police station", required=False),
    FormField("post_mortem_hospital", "Post-mortem conducted at", required=False),
]

FORMS: dict[SchemeId, dict[str, list[FormField]]] = {
    SchemeId.PMJJBY: {
        "pmjjby-claim-form": [*COMMON_DEATH, FormField("premium_debit", "Last premium debit"),
                              *NOMINEE],
    },
    SchemeId.PMSBY: {
        "pmsby-claim-form": [*COMMON_DEATH, FormField("premium_debit", "Last premium debit"),
                             *ACCIDENT, *NOMINEE],
    },
    SchemeId.RUPAY: {
        "rupay-claim-form": [*COMMON_DEATH, *ACCIDENT, *NOMINEE],
        "rupay-bank-declaration": [
            FormField("deceased_name", "Cardholder"),
            FormField("bank_name", "Card-issuing bank"),
            FormField("card_last_used", "Last successful card transaction (90-day criterion)"),
            FormField("card_issue_date", "Card issued on", required=False),
            FormField("accident_description", "Brief description of the accident (English)"),
            FormField("nominee_name", "Nominee"),
        ],
    },
}  # fmt: skip

ATTACHMENT_LABELS = {
    DocType.DEATH_CERTIFICATE: "Death certificate (original or attested copy)",
    DocType.PASSBOOK: "Passbook pages showing the account and recent entries",
    DocType.BANK_STATEMENT: "Bank statement",
    DocType.FIR: "FIR / police report",
    DocType.POST_MORTEM: "Post-mortem report",
    DocType.ID_PROOF: "ID proof (Aadhaar, masked) of the nominee",
}

SIGN_OFF = [
    "Claim form signed or thumb-printed by the nominee",
    "Nominee's cancelled cheque or passbook copy for payment",
    "Originals or attested copies carried to the branch",
]
