"""Mock partner portal: stands in for the bank / insurer claim desk in R0.

POST /claims checks the filing (known scheme, every form field filled, at least one
attachment) and returns an acknowledgement and claim number, or 422 with the reasons.
Repeating the same idempotency key returns the first receipt, so a retried submission never
files twice. GET /claims/{claim_number}/status advances submitted -> under_review -> paid on
each poll, so the demo can show the full lifecycle quickly. State is kept in a JSON file
(MOCK_PORTAL_STATE, default data/mock_portal.json) so a restart does not lose claims.
"""

import json
import os
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

app = FastAPI(title="Mock partner portal")

Status = Literal["submitted", "under_review", "paid"]
_NEXT: dict[str, Status] = {"submitted": "under_review", "under_review": "paid", "paid": "paid"}

# Fixed payouts for the three R0 schemes, used only to fake a "paid" amount.
_PAYOUT = {"pmjjby": 200_000, "pmsby": 200_000, "rupay": 200_000}


class Field(BaseModel):
    name: str
    value: str | None


class Form(BaseModel):
    template_id: str
    fields: list[Field]


class Attachment(BaseModel):
    doc_id: str
    label: str
    order: int


class ClaimIn(BaseModel):
    idempotency_key: str
    scheme_id: str
    claim_ref: str  # the filer's own claim id
    forms: list[Form]
    attachments: list[Attachment] = []
    cover_letter: str = ""


class Receipt(BaseModel):
    ack_id: str
    claim_number: str
    submitted_at: datetime


class ClaimStatus(BaseModel):
    claim_number: str
    status: Status
    paid_amount_inr: int | None = None


class Filed(BaseModel):
    receipt: Receipt
    claim: ClaimIn
    status: Status = "submitted"


_lock = threading.Lock()


def _state_file() -> Path:
    default = Path(__file__).resolve().parents[1] / "data" / "mock_portal.json"
    return Path(os.environ.get("MOCK_PORTAL_STATE", default))


def _load() -> dict[str, Filed]:
    f = _state_file()
    if not f.exists():
        return {}
    return {k: Filed.model_validate(v) for k, v in json.loads(f.read_text("utf-8")).items()}


def _save(filed: dict[str, Filed]) -> None:
    f = _state_file()
    f.parent.mkdir(parents=True, exist_ok=True)
    tmp = f.with_suffix(".tmp")
    tmp.write_text(json.dumps({k: v.model_dump(mode="json") for k, v in filed.items()}), "utf-8")
    tmp.replace(f)


def _problems(claim: ClaimIn) -> list[str]:
    out = []
    if claim.scheme_id not in _PAYOUT:
        out.append(f"unknown scheme {claim.scheme_id}")
    if not claim.forms:
        out.append("no claim form")
    for form in claim.forms:
        out += [f"{form.template_id}.{f.name} is empty" for f in form.fields if not f.value]
    if not claim.attachments:
        out.append("no documents attached")
    return out


@app.post("/claims", response_model=Receipt)
def submit_claim(claim: ClaimIn) -> Receipt:
    with _lock:
        filed = _load()  # keyed by idempotency key
        if claim.idempotency_key in filed:
            return filed[claim.idempotency_key].receipt
        if problems := _problems(claim):
            raise HTTPException(422, {"rejected": problems})
        n = len(filed) + 1
        receipt = Receipt(
            ack_id=f"ACK-{n:05d}",
            claim_number=f"MOCK-{claim.scheme_id.upper()}-{n:05d}",
            submitted_at=datetime.now(UTC),
        )
        filed[claim.idempotency_key] = Filed(receipt=receipt, claim=claim)
        _save(filed)
        return receipt


@app.get("/claims", response_model=list[Filed])
def list_claims() -> list[Filed]:
    """What the claim desk has received (for the demo)."""
    return list(_load().values())


@app.get("/claims/{claim_number}/status", response_model=ClaimStatus)
def claim_status(claim_number: str) -> ClaimStatus:
    with _lock:
        filed = _load()
        hit = next((f for f in filed.values() if f.receipt.claim_number == claim_number), None)
        if hit is None:
            raise HTTPException(404, "unknown claim number")
        current = hit.status
        hit.status = _NEXT[current]
        _save(filed)
    paid = _PAYOUT[hit.claim.scheme_id] if current == "paid" else None
    return ClaimStatus(claim_number=claim_number, status=current, paid_amount_inr=paid)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
