from datetime import datetime
from enum import StrEnum

from suraksha.contracts.common import Contract


class Channel(StrEnum):
    MOCK_PORTAL = "mock_portal"  # R0
    BANK_BRANCH = "bank_branch"
    INSURER_PORTAL = "insurer_portal"


class SubmissionReceipt(Contract):
    """Produced by the submission service, the only component that calls outside the system."""

    claim_id: str
    idempotency_key: str  # "<claim_id>:v<packet version>"
    channel: Channel
    ack_id: str
    claim_number: str
    submitted_at: datetime
