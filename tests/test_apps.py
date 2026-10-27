from fastapi.testclient import TestClient

from mock_portal.app import app as portal_app
from suraksha.api.main import app as api_app


def test_api_health():
    assert TestClient(api_app).get("/health").json()["status"] == "ok"


def test_mock_portal_is_idempotent_and_advances_status():
    client = TestClient(portal_app)
    body = {"idempotency_key": "cl-test:v1", "scheme_id": "pmjjby", "packet": {}}
    first = client.post("/claims", json=body).json()
    again = client.post("/claims", json=body).json()
    assert first == again

    number = first["claim_number"]
    seen = [client.get(f"/claims/{number}/status").json()["status"] for _ in range(3)]
    assert seen == ["submitted", "under_review", "paid"]
    assert client.get(f"/claims/{number}/status").json()["paid_amount_inr"] == 200_000
