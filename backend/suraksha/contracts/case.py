from datetime import date
from enum import StrEnum

from pydantic import Field

from suraksha.contracts.common import Contract, EventType, VaultToken


class Deceased(Contract):
    name_token: VaultToken
    date_of_death: date | None = None
    place_of_death: str | None = None  # district / state, not a full address
    event_type: EventType = EventType.UNKNOWN
    age_at_death: int | None = Field(default=None, ge=0, le=120)


class Relationship(StrEnum):
    SPOUSE = "spouse"
    CHILD = "child"
    PARENT = "parent"
    SIBLING = "sibling"
    OTHER = "other"


class Claimant(Contract):
    claimant_id: str
    name_token: VaultToken
    relationship: Relationship
    is_nominee: bool | None = None  # None until a document confirms it
    phone_token: VaultToken | None = None
    bank_account_token: VaultToken | None = None  # payouts go only to the nominee's account


class AssetType(StrEnum):
    BANK_ACCOUNT = "bank_account"
    DEBIT_CARD = "debit_card"
    PF_ACCOUNT = "pf_account"
    LOAN = "loan"
    VEHICLE = "vehicle"
    OTHER = "other"


class Asset(Contract):
    asset_id: str
    type: AssetType
    institution: str | None = None  # e.g. bank name
    identifier_token: VaultToken | None = None  # account / card number lives in the vault
    card_variant: str | None = None  # RuPay classic / platinum etc., sets the cover amount
    last_txn_date: date | None = None  # last successful card transaction (RuPay 90-day rule)
    card_issue_date: date | None = None  # sets the RuPay payout tier (28 Aug 2018 cut-off)
    source: str  # "family_statement" or an ExtractedDocument id


class CaseFacts(Contract):
    """Produced by the conversation agent (intake fields only) and enriched by documents."""

    case_id: str
    deceased: Deceased
    claimants: list[Claimant] = []
    assets: list[Asset] = []
