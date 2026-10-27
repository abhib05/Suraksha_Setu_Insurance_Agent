"""M1 done-when: open a case, record consent, store a document, and every step is in
audit_events. Plus the guarantees each piece makes on its own."""

import io

import pytest
from fastapi.testclient import TestClient
from PIL import Image, ImageDraw, ImageFont
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from suraksha import audit
from suraksha.api.main import app
from suraksha.db.base import session_scope
from suraksha.db.models import Document, VaultSecret
from suraksha.services import docstore, vault
from suraksha.services.docstore.aadhaar import is_aadhaar, mask_text, verhoeff_check_digit

pytestmark = pytest.mark.usefixtures("migrated_db")

AADHAAR = "23456789012" + verhoeff_check_digit("23456789012")


def _id_card_png() -> bytes:
    img = Image.new("RGB", (900, 300), "white")
    d = ImageDraw.Draw(img)
    font = ImageFont.load_default(size=40)
    d.text((40, 40), "Name: Test Person", fill="black", font=font)
    d.text((40, 130), f"{AADHAAR[:4]} {AADHAAR[4:8]} {AADHAAR[8:]}", fill="black", font=font)
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


@pytest.fixture
def client(docstore_dir) -> TestClient:
    return TestClient(app)


def test_case_consent_document_all_audited(client):
    case_id = client.post("/cases", json={}).json()["case_id"]

    # No consent yet: the write is refused.
    r = client.post(f"/cases/{case_id}/documents", files={"file": ("id.png", b"x", "image/png")})
    assert r.status_code == 403

    r = client.post(f"/cases/{case_id}/consent", json={"given_by_name": "Sunita Devi"})
    assert r.status_code == 201
    assert r.json()["given_by"].startswith("tok_")

    r = client.post(
        f"/cases/{case_id}/documents", files={"file": ("aadhaar.png", _id_card_png(), "image/png")}
    )
    assert r.status_code == 201, r.text
    doc = r.json()
    assert doc["masking_status"] == "masked" and doc["aadhaar_masked"] == 1

    actions = [e["action"] for e in client.get(f"/cases/{case_id}/audit").json()]
    # Without a model key the upload still succeeds; extraction is marked pending.
    assert actions[:3] == ["case.opened", "consent.given", "document.stored"]
    assert set(actions[3:]) <= {"document.extraction_pending", "document.extracted"}
    assert client.get("/audit/verify").json()["ok"] is True

    # After withdrawal, writes are refused again.
    client.post(f"/cases/{case_id}/consent/withdraw")
    r = client.post(f"/cases/{case_id}/documents", files={"file": ("p.png", b"x", "image/png")})
    assert r.status_code == 403


def test_no_plaintext_name_anywhere_but_vault(client):
    case_id = client.post("/cases", json={}).json()["case_id"]
    token = client.post(
        f"/cases/{case_id}/consent", json={"given_by_name": "Unique Name Xyzzy"}
    ).json()["given_by"]
    with session_scope() as s:
        dump = s.execute(text("SELECT row_to_json(c)::text FROM consent c")).scalars().all()
        dump += s.execute(text("SELECT row_to_json(a)::text FROM audit_event a")).scalars().all()
        assert not any("Xyzzy" in row for row in dump)
        assert b"Xyzzy" not in s.get(VaultSecret, token).ciphertext
        assert vault.reveal(s, token, actor="test", purpose="check") == "Unique Name Xyzzy"


def test_audit_log_rejects_update_delete_truncate():
    for sql in (
        "UPDATE audit_event SET actor = 'x'",
        "DELETE FROM audit_event",
        "TRUNCATE audit_event",
    ):
        with pytest.raises(DBAPIError, match="append-only"), session_scope() as s:
            audit.append(s, actor="test", action="test.seed")
            s.execute(text(sql))


def test_audit_chain_detects_tampering():
    with session_scope() as s:
        audit.append(s, actor="test", action="test.a", input={"x": 1})
        e = audit.append(s, actor="test", action="test.b", output="y")
        assert e.input_hash is None and e.output_hash == audit.content_hash("y")
        assert audit.verify_chain(s).ok
        # Simulate someone with superuser rights editing history behind the trigger.
        s.execute(text("ALTER TABLE audit_event DISABLE TRIGGER audit_event_no_update_delete"))
        s.execute(text("UPDATE audit_event SET actor = 'mallory' WHERE id = :i"), {"i": e.id})
        check = audit.verify_chain(s)
        assert not check.ok and check.first_bad_id == e.id
        s.rollback()


def test_original_encrypted_and_working_copy_masked(client, docstore_dir):
    case_id = client.post("/cases", json={}).json()["case_id"]
    client.post(f"/cases/{case_id}/consent", json={"given_by_name": "A"})
    png = _id_card_png()
    doc_id = client.post(
        f"/cases/{case_id}/documents", files={"file": ("a.png", png, "image/png")}
    ).json()["doc_id"]
    with session_scope() as s:
        doc = s.get(Document, doc_id)
        assert png not in (docstore_dir / case_id / f"{doc_id}.orig.enc").read_bytes()
        assert docstore.original(s, doc, actor="test", purpose="filing") == png
        assert docstore.working_copy(doc) != png


def test_pdf_has_no_working_copy_until_pdf_masking_exists(client):
    case_id = client.post("/cases", json={}).json()["case_id"]
    client.post(f"/cases/{case_id}/consent", json={"given_by_name": "A"})
    doc_id = client.post(
        f"/cases/{case_id}/documents", files={"file": ("f.pdf", b"%PDF-1.4", "application/pdf")}
    ).json()["doc_id"]
    with session_scope() as s, pytest.raises(docstore.NotMaskedError):
        docstore.working_copy(s.get(Document, doc_id))


def test_aadhaar_text_rules():
    assert is_aadhaar(AADHAAR)
    assert not is_aadhaar("1" + AADHAAR[1:])  # cannot start with 0/1
    masked, n = mask_text(f"UID {AADHAAR[:4]}-{AADHAAR[4:8]}-{AADHAAR[8:]}, mob 9876543210")
    assert n == 1 and masked == f"UID XXXX XXXX {AADHAAR[8:]}, mob 9876543210"
