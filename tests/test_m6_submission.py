"""M6: the submission adapter files case A's packets with the mock portal, reveals names only
at that boundary, never files twice for one idempotency key, and turns a portal rejection
into a final error the workflow hands to a reviewer."""

import pytest
from fastapi.testclient import TestClient

from mock_portal.app import app as portal_app
from suraksha.contracts import SchemeId
from suraksha.services.submission.portal import PortalClient, SubmissionRejected
from tests.test_m5_packets import packet_for


@pytest.fixture
def portal(tmp_path, monkeypatch):
    monkeypatch.setenv("MOCK_PORTAL_STATE", str(tmp_path / "portal.json"))
    http = TestClient(portal_app)
    return PortalClient("http://portal", client=http), http


@pytest.mark.parametrize("scheme", list(SchemeId))
def test_case_a_packets_file_and_reach_paid(portal, scheme):
    client, http = portal
    p, *_ = packet_for("case_a", scheme)
    revealed = []

    def reveal(token):
        revealed.append(token)
        return "Demo Name"

    receipt = client.submit(p, reveal)
    assert receipt.claim_number.startswith(f"MOCK-{scheme.value.upper()}-")
    assert receipt.idempotency_key == f"{p.claim_id}:v1"
    assert revealed, "names are revealed at the boundary"
    sent = http.get("/claims").json()[0]["claim"]
    values = [f["value"] for form in sent["forms"] for f in form["fields"]]
    assert "Demo Name" in values and not any(v.startswith("tok_") for v in values)
    assert len(sent["attachments"]) == len(p.attachments)
    seen = [client.status(receipt.claim_number) for _ in range(3)]
    assert [s for s, _ in seen] == ["submitted", "under_review", "paid"]
    assert seen[-1][1] == 200_000


def test_retry_with_same_key_does_not_file_twice(portal):
    client, http = portal
    p, *_ = packet_for("case_a", SchemeId.PMJJBY)
    first, again = client.submit(p), client.submit(p)
    assert first.claim_number == again.claim_number and first.ack_id == again.ack_id
    v2 = client.submit(p.model_copy(update={"version": 2}))
    assert v2.claim_number != first.claim_number  # a new version is a new filing
    assert len(http.get("/claims").json()) == 2


def test_portal_survives_restart(portal):
    client, _ = portal
    p, *_ = packet_for("case_a", SchemeId.PMSBY)
    receipt = client.submit(p)
    fresh = PortalClient("http://portal", client=TestClient(portal_app))  # state is on disk
    assert fresh.status(receipt.claim_number)[0] == "submitted"


def test_rejection_is_final_with_reasons(portal):
    client, _ = portal
    p, *_ = packet_for("case_a", SchemeId.RUPAY)
    form = p.forms[0]
    fields = [f.model_copy(update={"value": None}) if f.name == "place_of_death" else f
              for f in form.fields]  # fmt: skip
    bad = p.model_copy(update={"forms": [form.model_copy(update={"fields": fields}),
                                         *p.forms[1:]]})  # fmt: skip
    with pytest.raises(SubmissionRejected, match="place_of_death is empty"):
        client.submit(bad)
    with pytest.raises(SubmissionRejected, match="no documents attached"):
        client.submit(p.model_copy(update={"attachments": [], "version": 3}))


# --- review console reads -----------------------------------------------------------


@pytest.mark.usefixtures("migrated_db")
def test_review_queue_lists_claims_waiting_on_a_reviewer_first(docstore_dir):
    from suraksha.activities.store import DbStore
    from suraksha.agents.filing.agent import build_packet
    from suraksha.agents.stubs import _fixture
    from suraksha.agents.verifier.agent import verify_packet
    from suraksha.api.main import app
    from suraksha.contracts import CaseFacts, ExtractedDocument
    from suraksha.services import rules
    from suraksha.services.rules.facts import rule_facts
    from suraksha.services.rules.matchers import match_entitlements

    api = TestClient(app)
    case_id = api.post("/cases", json={}).json()["case_id"]
    facts = CaseFacts.model_validate(_fixture("case_a", "facts", case_id))
    docs = [ExtractedDocument.model_validate(d) for d in _fixture("case_a", "documents", case_id)]
    ents = match_entitlements(facts, docs)
    rf = rule_facts(facts, docs)
    results = [rules.check_entitlement(e, rf) for e in ents]
    store = DbStore()
    store.save_facts(facts)
    store.save_entitlements(ents, results)
    claim_ids = []
    for e, r in zip(ents, results, strict=True):
        cid = f"clm_{case_id[-6:]}_{e.scheme_id.value}"
        claim_ids.append(cid)
        store.open_claim(case_id, cid, e)
        p = build_packet(cid, e, facts, docs, 1, use_model=False)
        store.save_packet(p, verify_packet(p, e, facts, docs, r, use_model=False))
    store.set_claim_status(claim_ids[0], "submitted", "MOCK-X-1")
    store.set_claim_status(claim_ids[1], "awaiting_approval")
    store.set_claim_status(claim_ids[2], "building_packet")
    store.create_task(case_id, claim_ids[2], "verifier_failed", "v1: broken", "reviewer")

    rows = api.get("/review/claims", params={"case_id": case_id}).json()
    assert [r["claim_id"] for r in rows][:2] == [claim_ids[1], claim_ids[2]] or \
        [r["claim_id"] for r in rows][:2] == [claim_ids[2], claim_ids[1]]
    assert rows[-1]["claim_id"] == claim_ids[0] and rows[-1]["claim_number"] == "MOCK-X-1"
    assert all(r["verdict"]["passed"] and r["packet_version"] == 1 for r in rows)

    detail = api.get(f"/review/claims/{claim_ids[2]}").json()
    assert detail["open_tasks"] == 1 and detail["tasks"][0]["kind"] == "verifier_failed"
    assert [v["version"] for v in detail["versions"]] == [1]
    assert detail["expected_payout_inr"] == 200_000 and detail["decisions"] == []


def test_rules_endpoint_gives_text_and_source_for_every_rule():
    from suraksha.api.main import app

    rules = {r["id"]: r for r in TestClient(app).get("/rules").json()}
    assert len(rules) == 9
    r2 = rules["RUPAY-R2"]
    assert "90" in r2["title"] + r2["explain_fail"]
    assert all(r["source_url"].startswith("https://") for r in rules.values()), \
        [r["id"] for r in rules.values() if not r["source_url"]]
