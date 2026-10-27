from datetime import datetime
from enum import StrEnum

from suraksha.contracts.common import Contract, VaultToken


class ConsentPurpose(StrEnum):
    PREPARE_CLAIMS = "prepare_claims"  # find covers, collect documents, build packets
    SHARE_WITH_PARTNER = "share_with_partner"  # send an approved packet to the filing desk


class ConsentMethod(StrEnum):
    CHAT = "chat"
    VOICE = "voice"
    ASSISTED = "assisted"  # a field assistant recorded it in person
    THUMBPRINT = "thumbprint"


class ConsentRecord(Contract):
    """Produced by the conversation agent. No data write happens on a case without one."""

    case_id: str
    purpose: ConsentPurpose
    scope: list[str]  # data categories covered, e.g. ["identity", "bank", "documents"]
    given_by: VaultToken
    method: ConsentMethod
    timestamp: datetime
    withdrawn_at: datetime | None = None

    @property
    def active(self) -> bool:
        return self.withdrawn_at is None
