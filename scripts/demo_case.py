"""`uv run poe demo-case c`: run one eval case through the running app's API, the way a family
would in the console: consent, a short chat, the case's photos, finish. Use it to prepare a
case before a demo (the demo shows case C's briefing rather than typing it live).

Needs the stack running (`uv run poe demo`). Prints the case id and a console link.
"""

import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
API = "http://localhost:8000"

# What the family says. Names match the synthetic photos (scripts/make_fake_documents.py).
CASES = {
    "a": ("Sunita Sample", [
        "I am his wife and the nominee. My husband Ramesh Sample died in a road accident in "
        "Mysuru on 14 September 2026. He was 41.",
        "Yes. He had a savings account at Kaveri Gramin Bank and a RuPay Jan Dhan card.",
        "He used the card at the ATM a few days before the accident.",
        "Yes, all correct.",
    ]),
    "c": ("Kavita Sample", [
        "I am his wife and the nominee. My husband Suresh Sample died of an illness in "
        "Tumakuru on 20 August 2026. He was 47.",
        "Yes. He had a savings account at Kaveri Gramin Bank and a RuPay Jan Dhan card from "
        "2016.",
        "I don't think he used the card for a long time.",
        "Yes, all correct.",
    ]),
}


def main() -> None:
    key = (sys.argv[1] if len(sys.argv) > 1 else "c").lower().removeprefix("case_")
    speaker, lines = CASES[key]
    photos = sorted((ROOT / "evals" / "cases" / f"case_{key}" / "documents").glob("d*.jpg"))
    with httpx.Client(base_url=API, timeout=180) as api:
        case_id = api.post("/cases", json={"channel": "web_chat"}).json()["case_id"]
        api.post(f"/cases/{case_id}/workflow", json={"wait_for_intake": True}).raise_for_status()
        api.post(f"/cases/{case_id}/chat", json={"message": "Hello"}).raise_for_status()
        api.post(f"/cases/{case_id}/consent",
                 json={"given_by_name": speaker, "method": "chat"}).raise_for_status()  # fmt: skip

        def upload(p: Path) -> None:
            files = {"file": (p.name, p.read_bytes(), "image/jpeg")}
            api.post(f"/cases/{case_id}/documents", files=files).raise_for_status()

        with ThreadPoolExecutor() as pool:  # photos are read while the chat goes on
            reads = [pool.submit(upload, p) for p in photos]
            for line in ["I agree.", *lines]:
                reply = api.post(f"/cases/{case_id}/chat", json={"message": line}).json()
                print(f"> {line}\n  {reply['reply']}\n")
            for r in reads:
                r.result()
        api.post(f"/cases/{case_id}/intake/finish").raise_for_status()
        for _ in range(60):
            stage = api.get(f"/cases/{case_id}/workflow").json().get("stage")
            if stage in ("awaiting_choice", "closed"):
                break
            time.sleep(2)
    print(f"case {case_id}: {stage}\nopen http://localhost:5173/?case={case_id}")


if __name__ == "__main__":
    main()
