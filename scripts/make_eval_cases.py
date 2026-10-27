"""Write evals/cases/{case_a,case_b,case_c}: facts.json, documents.json, expected.json.

All data is invented. facts.json is what the interview should capture; documents.json is
what the document agent should extract (until M4 adds real photos); expected.json is what
the system must conclude. Run: uv run python scripts/make_eval_cases.py
"""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from suraksha.contracts import CaseFacts, ExtractedDocument  # noqa: E402


def doc(case, n, doc_type, fields):
    return {"doc_id": f"{case}-d{n}", "case_id": case, "doc_type": doc_type,
            "fields": [{"name": k, "value": v, "confidence": 0.96} for k, v in fields],
            "masked_copy_ref": f"evals/cases/{case}/documents/d{n}.masked.png",
            "sha256": f"{n:064x}"}  # fmt: skip


def facts(case, death, event, age, place, card):
    return {
        "case_id": case,
        "deceased": {"name_token": f"tok_{case}_deceased", "date_of_death": death,
                     "place_of_death": place, "event_type": event, "age_at_death": age},
        "claimants": [{"claimant_id": f"{case}-c1", "name_token": f"tok_{case}_spouse",
                       "relationship": "spouse", "is_nominee": True,
                       "bank_account_token": f"tok_{case}_spouse_acct"}],
        "assets": [
            {"asset_id": f"{case}-a1", "type": "bank_account", "institution": "State Bank of India",
             "identifier_token": f"tok_{case}_acct", "source": "family_statement"},
            {"asset_id": f"{case}-a2", "type": "debit_card", "institution": "State Bank of India",
             "identifier_token": f"tok_{case}_card", "source": f"{case}-d1", **card},
        ],
    }  # fmt: skip


PMJJBY_ALL = ["PMJJBY-R1", "PMJJBY-R2", "PMJJBY-R3"]

CASES = {
    # Road accident, every cover active and proven: three claims, Rs 6 lakh.
    "case_a": dict(
        facts=facts(
            "case_a",
            "2026-09-14",
            "accident",
            41,
            "Mysuru, Karnataka",
            {"card_variant": "rupay_pmjdy", "card_issue_date": "2019-03-02"},
        ),
        documents=[
            doc(
                "case_a",
                1,
                "passbook",
                [
                    ("txn", "2025-05-25|PMSBY PREMIUM|20|DR"),
                    ("txn", "2025-05-28|PMJJBY PREM RENEWAL|436|DR"),
                    ("txn", "2026-05-25|PMSBY PREMIUM|20|DR"),
                    ("txn", "2026-05-28|PMJJBY PREM RENEWAL|436|DR"),
                    ("txn", "2026-09-10|ATM WDL RUPAY MYSURU|2000|DR"),
                ],
            ),
            doc("case_a", 2, "death_certificate", [("date_of_death", "2026-09-14")]),
            doc("case_a", 3, "fir", [("cause", "road accident, two-wheeler hit by truck")]),
            doc("case_a", 4, "post_mortem", [("cause", "head injury")]),
            doc("case_a", 5, "id_proof", [("aadhaar_masked", "XXXX XXXX 4821")]),
        ],
        expected={
            "covers": {"pmjjby": "strong", "pmsby": "strong", "rupay": "strong"},
            "rules": {
                "pmjjby": {"status": "eligible", "rule_ids": PMJJBY_ALL},
                "pmsby": {
                    "status": "eligible",
                    "rule_ids": ["PMSBY-R1", "PMSBY-R2", "PMSBY-R3", "PMSBY-R4"],
                },
                "rupay": {"status": "eligible", "rule_ids": ["RUPAY-R1", "RUPAY-R2"]},
            },
            "payout_inr": {"pmjjby": 200000, "pmsby": 200000, "rupay": 200000},
            "missing_documents": {"pmjjby": [], "pmsby": [], "rupay": []},
        },
    ),
    # Accident, but the card's recent use is not visible and the death certificate is not
    # uploaded yet: RuPay is unknown with a question, every claim waits for one document.
    "case_b": dict(
        facts=facts(
            "case_b",
            "2026-08-03",
            "accident",
            36,
            "Hubballi, Karnataka",
            {"card_variant": "rupay_pmjdy", "card_issue_date": "2017-11-20"},
        ),
        documents=[
            doc(
                "case_b",
                1,
                "passbook",
                [
                    ("txn", "2026-05-26|PMJJBY PREMIUM|436|DR"),
                    ("txn", "2026-05-26|PMSBY PREMIUM|20|DR"),
                    ("txn", "2026-06-02|SALARY CREDIT|9500|CR"),
                ],
            ),
            doc("case_b", 3, "fir", [("cause", "fall from construction site scaffolding")]),
            doc("case_b", 4, "post_mortem", [("cause", "multiple injuries")]),
            doc("case_b", 5, "id_proof", [("aadhaar_masked", "XXXX XXXX 1190")]),
        ],
        expected={
            "covers": {"pmjjby": "strong", "pmsby": "strong", "rupay": "strong"},
            "rules": {
                "pmjjby": {"status": "eligible", "rule_ids": PMJJBY_ALL},
                "pmsby": {
                    "status": "eligible",
                    "rule_ids": ["PMSBY-R1", "PMSBY-R2", "PMSBY-R3", "PMSBY-R4"],
                },
                "rupay": {
                    "status": "unknown",
                    "rule_ids": ["RUPAY-R2"],
                    "missing_facts": ["card_last_txn_date"],
                },
            },
            "payout_inr": {"pmjjby": 200000, "pmsby": 200000, "rupay": 100000},
            "missing_documents": {
                "pmjjby": ["death_certificate"],
                "pmsby": ["death_certificate"],
                "rupay": ["death_certificate"],
            },
        },
    ),
    # Death from illness two weeks after first joining PMJJBY: nothing pays, and the family
    # is told why in plain words.
    "case_c": dict(
        facts=facts(
            "case_c",
            "2026-08-20",
            "illness",
            47,
            "Tumakuru, Karnataka",
            {"card_variant": "rupay_pmjdy", "card_issue_date": "2016-01-15"},
        ),
        documents=[
            doc(
                "case_c",
                1,
                "passbook",
                [
                    ("txn", "2026-03-01|ATM WDL RUPAY|1500|DR"),
                    ("txn", "2026-05-27|PMSBY PREMIUM|20|DR"),
                    ("txn", "2026-08-05|PMJJBY NEW ENROLMENT|436|DR"),
                ],
            ),
            doc(
                "case_c",
                2,
                "death_certificate",
                [("date_of_death", "2026-08-20"), ("cause", "illness")],
            ),
            doc("case_c", 5, "id_proof", [("aadhaar_masked", "XXXX XXXX 7302")]),
        ],
        expected={
            "covers": {"pmjjby": "strong", "pmsby": "strong", "rupay": "strong"},
            "rules": {
                "pmjjby": {"status": "not_eligible", "rule_ids": ["PMJJBY-R2"]},
                "pmsby": {"status": "not_eligible", "rule_ids": ["PMSBY-R2"]},
                "rupay": {"status": "not_eligible", "rule_ids": ["RUPAY-R1", "RUPAY-R2"]},
            },
            "payout_inr": {"pmjjby": 200000, "pmsby": 200000, "rupay": 100000},
            "missing_documents": {},
        },
    ),
}

for name, c in CASES.items():
    folder = ROOT / "evals" / "cases" / name
    folder.mkdir(parents=True, exist_ok=True)
    CaseFacts.model_validate(c["facts"])  # contracts must accept every fixture
    for d in c["documents"]:
        ExtractedDocument.model_validate(d)
    for fname, data in (("facts", c["facts"]), ("documents", c["documents"]),
                        ("expected", c["expected"])):  # fmt: skip
        (folder / f"{fname}.json").write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    (folder / ".gitkeep").unlink(missing_ok=True)
    print("wrote", folder.relative_to(ROOT))
