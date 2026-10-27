"""Submission adapter for the R0 mock partner portal: the only code that sends a claim
outside the system.

The packet's vault tokens are revealed here, at the boundary, through `reveal` (the caller
audits each reveal). The idempotency key is the packet's own ("<claim_id>:v<version>"), so a
retried call after a timeout returns the first receipt instead of filing twice. A portal
rejection (422) is final for that packet version and is raised as `SubmissionRejected`; a
network error or 5xx is raised as-is so the workflow retries it.
"""

from collections.abc import Callable
from datetime import datetime

import httpx

from suraksha.contracts import ClaimPacket, SubmissionReceipt
from suraksha.contracts.submission import Channel

REJECTED = "SubmissionRejected"  # the activity error type the claim workflow handles


class SubmissionRejected(RuntimeError):
    """The claim desk refused the filing; a person must look at it."""


class PortalClient:
    def __init__(self, base_url: str, client: httpx.Client | None = None, timeout: float = 15):
        self.http = client or httpx.Client(base_url=base_url, timeout=timeout)

    def submit(
        self, packet: ClaimPacket, reveal: Callable[[str], str] = lambda token: token
    ) -> SubmissionReceipt:
        body = {
            "idempotency_key": packet.idempotency_key,
            "scheme_id": packet.scheme_id.value,
            "claim_ref": packet.claim_id,
            "forms": [
                {"template_id": form.template_id,
                 "fields": [{"name": f.name, "value": _reveal(f.value, reveal)}
                            for f in form.fields]}
                for form in packet.forms
            ],
            "attachments": [a.model_dump() for a in packet.attachments],
            "cover_letter": packet.cover_letter,
        }  # fmt: skip
        r = self.http.post("/claims", json=body)
        if r.status_code == 422:
            raise SubmissionRejected(f"portal rejected {packet.idempotency_key}: {_reason(r)}")
        r.raise_for_status()
        data = r.json()
        return SubmissionReceipt(
            claim_id=packet.claim_id,
            idempotency_key=packet.idempotency_key,
            channel=Channel.MOCK_PORTAL,
            ack_id=data["ack_id"],
            claim_number=data["claim_number"],
            submitted_at=datetime.fromisoformat(data["submitted_at"]),
        )

    def status(self, claim_number: str) -> tuple[str, int | None]:
        r = self.http.get(f"/claims/{claim_number}/status")
        r.raise_for_status()
        data = r.json()
        return data["status"], data.get("paid_amount_inr")


def _reveal(value: str | None, reveal: Callable[[str], str]) -> str | None:
    return reveal(value) if value and value.startswith("tok_") else value


def _reason(r: httpx.Response) -> str:
    try:
        detail = r.json().get("detail")
    except ValueError:
        return r.text[:300]
    if isinstance(detail, dict) and "rejected" in detail:
        return "; ".join(detail["rejected"])
    return str(detail)[:300]
