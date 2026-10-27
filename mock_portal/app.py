"""Mock partner portal: stands in for the bank / insurer claim desk in R0.

POST /claims returns an acknowledgement and claim number; repeating the same idempotency key
returns the first receipt. GET /claims/{claim_number}/status advances submitted -> under_review
-> paid on each poll, so the demo can show the full lifecycle quickly.
"""

from datetime import UTC, datetime
from typing import Literal

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

app = FastAPI(title="Mock partner portal")

Status = Literal["submitted", "under_review", "paid"]
_NEXT: dict[str, Status] = {"submitted": "under_review", "under_review": "paid", "paid": "paid"}


class ClaimIn(BaseModel):
    idempotency_key: str
    scheme_id: str
    packet: dict


class Receipt(BaseModel):
    ack_id: str
    claim_number: str
    submitted_at: datetime


class ClaimStatus(BaseModel):
    claim_number: str
    status: Status
    paid_amount_inr: int | None = None


_receipts: dict[str, Receipt] = {}
_status: dict[str, Status] = {}
_scheme: dict[str, str] = {}

# Fixed payouts for the three R0 schemes, used only to fake a "paid" amount.
_PAYOUT = {"pmjjby": 200_000, "pmsby": 200_000, "rupay": 200_000}


@app.post("/claims", response_model=Receipt)
def submit_claim(claim: ClaimIn) -> Receipt:
    if claim.idempotency_key in _receipts:
        return _receipts[claim.idempotency_key]
    n = len(_receipts) + 1
    receipt = Receipt(
        ack_id=f"ACK-{n:05d}",
        claim_number=f"MOCK-{claim.scheme_id.upper()}-{n:05d}",
        submitted_at=datetime.now(UTC),
    )
    _receipts[claim.idempotency_key] = receipt
    _status[receipt.claim_number] = "submitted"
    _scheme[receipt.claim_number] = claim.scheme_id
    return receipt


@app.get("/claims/{claim_number}/status", response_model=ClaimStatus)
def claim_status(claim_number: str) -> ClaimStatus:
    if claim_number not in _status:
        raise HTTPException(404, "unknown claim number")
    current = _status[claim_number]
    _status[claim_number] = _NEXT[current]
    paid = _PAYOUT.get(_scheme[claim_number]) if current == "paid" else None
    return ClaimStatus(claim_number=claim_number, status=current, paid_amount_inr=paid)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
