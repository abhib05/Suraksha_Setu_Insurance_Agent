"""`uv run poe eval`: score the pipeline on every case in evals/cases against expected.json.

For each case: the document agent reads the case's photos (Aadhaar masked first, as in the
app), discovery finds covers, the rules decide eligibility, and the filing agent and
verifier build and check one packet per cover. Scored against expected.json:

- covers:   each expected scheme found with the expected evidence strength, nothing extra
- rules:    status, cited rule ids and (for unknown) the missing facts
- payout:   the registry amount for each cover
- missing:  required documents the case does not hold
- packets:  the verifier passes exactly the packets that are eligible and complete, every
            field of a passing packet is filled and traced, and a blocked packet says why

Model calls replay from evals/recordings (no spend). `--record` calls the API for requests
not recorded yet; `--fixtures` skips the photos and uses documents.json (no model at all).
The scorecard is written to evals/results/latest.md; exit code 1 if any case fails.
"""

import json
import os
import sys
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
os.environ["LLM_MODE"] = "record" if "--record" in sys.argv else "replay"
FIXTURES = "--fixtures" in sys.argv
sys.path.insert(0, str(ROOT / "backend"))

from suraksha.agents.discovery.agent import discover  # noqa: E402
from suraksha.agents.document.agent import read_document  # noqa: E402
from suraksha.agents.filing.agent import build_packet  # noqa: E402
from suraksha.agents.verifier.agent import verify_packet  # noqa: E402
from suraksha.contracts import CaseFacts, ExtractedDocument, Severity  # noqa: E402
from suraksha.services import rules  # noqa: E402
from suraksha.services.docstore.aadhaar import mask_image  # noqa: E402
from suraksha.services.registry import required_documents  # noqa: E402
from suraksha.services.rules.facts import rule_facts  # noqa: E402
from suraksha.tools.llm import total_spend  # noqa: E402

CASES = ROOT / "evals" / "cases"
RESULTS = ROOT / "evals" / "results"
EVAL_TODAY = date(2026, 10, 28)  # fixed so recorded verifier calls replay on any day
USE_MODEL = not FIXTURES


@dataclass
class Score:
    case: str
    checks: list[tuple[str, bool, str]] = field(default_factory=list)

    def add(self, area: str, ok: bool, label: str) -> None:
        self.checks.append((area, ok, label))

    @property
    def passed(self) -> bool:
        return all(ok for _, ok, _ in self.checks)


def documents(case: str) -> list[ExtractedDocument]:
    fixtures = json.loads((CASES / case / "documents.json").read_text(encoding="utf-8"))
    if FIXTURES:
        return [ExtractedDocument.model_validate(d) for d in fixtures]
    out = []
    for photo in sorted((CASES / case / "documents").glob("d*.jpg")):
        masked, _ = mask_image(photo.read_bytes())
        out.append(read_document(case, f"{case}-{photo.stem}", masked).document)
    return out


def score_case(case: str) -> Score:
    sc = Score(case)
    expected = json.loads((CASES / case / "expected.json").read_text(encoding="utf-8"))
    facts = CaseFacts.model_validate_json((CASES / case / "facts.json").read_text("utf-8"))
    docs = documents(case)
    held = {d.doc_type for d in docs}

    ents, _ = discover(facts, docs, use_model=USE_MODEL)
    found = {e.scheme_id.value: e for e in ents}
    want_covers: dict = expected.get("covers", {})
    sc.add("covers", set(found) == set(want_covers),
           f"covers found {sorted(found)} (expected {sorted(want_covers)})")  # fmt: skip
    for scheme, strength in want_covers.items():
        if scheme in found:
            got = found[scheme].strength.value
            sc.add("covers", got == strength, f"{scheme} evidence {got} (expected {strength})")

    rf = rule_facts(facts, docs)
    for scheme, ent in found.items():
        want = expected.get("rules", {}).get(scheme)
        result = rules.check_entitlement(ent, rf)
        if want:
            ok = result.status.value == want["status"] and result.rule_ids == want["rule_ids"]
            if "missing_facts" in want:
                ok = ok and result.missing_facts == want["missing_facts"]
            label = f"{scheme} {result.status.value} {', '.join(result.rule_ids)}"
            if not ok:
                label += f" (expected {want['status']} {want['rule_ids']})"
            sc.add("rules", ok, label)
        pay = expected.get("payout_inr", {}).get(scheme)
        if pay is not None:
            got_pay = ent.expected_payout_inr
            sc.add("payout", got_pay == pay, f"{scheme} payout Rs {got_pay:,} (expected Rs {pay:,})")
        missing = [t.value for t in required_documents(ent.scheme_id) if t not in held]
        want_missing = expected.get("missing_documents", {}).get(scheme, [])
        if "missing_documents" in expected and scheme in expected["missing_documents"]:
            sc.add("missing", missing == want_missing,
                   f"{scheme} missing documents {missing or 'none'}")  # fmt: skip

        packet = build_packet(f"eval_{case}_{scheme}", ent, facts, docs, 1, use_model=USE_MODEL)
        verdict = verify_packet(packet, ent, facts, docs, result, use_model=USE_MODEL,
                                today=EVAL_TODAY)  # fmt: skip
        should_pass = result.status.value == "eligible" and not missing
        blockers = [f.reason for f in verdict.findings if f.severity is Severity.BLOCKER]
        if should_pass:
            filled = all(f.value and f.source_ref for form in packet.forms for f in form.fields)
            sc.add("packets", verdict.passed and filled,
                   f"{scheme} packet passes, every field filled and traced"
                   + ("" if verdict.passed else f" - blocked: {blockers[:2]}"))  # fmt: skip
        else:
            why = " ".join(blockers)
            explained = any(t in why for t in missing) or "ligib" in why
            label = f"{scheme} packet blocked with the reason ({len(blockers)} blocker(s))"
            sc.add("packets", not verdict.passed and explained, label)
        if USE_MODEL:
            ran = not any("AI review did not run" in f.reason for f in verdict.findings)
            sc.add("packets", ran, f"{scheme} Muse Wonder review ran")
    return sc


def scorecard(scores: list[Score]) -> str:
    mode = "documents.json fixtures, no model" if FIXTURES else "photos through the models"
    passed = sum(s.passed for s in scores)
    lines = [f"# Eval scorecard: {passed}/{len(scores)} cases pass", "",
             f"Mode: {mode}; LLM_MODE={os.environ['LLM_MODE']}.", "",
             "| Case | Covers | Rules | Payout | Missing docs | Packets | Result |",
             "|---|---|---|---|---|---|---|"]  # fmt: skip
    for s in scores:
        cells = []
        for area in ("covers", "rules", "payout", "missing", "packets"):
            hits = [ok for a, ok, _ in s.checks if a == area]
            cells.append(f"{sum(hits)}/{len(hits)}" if hits else "–")
        result = "PASS" if s.passed else "FAIL"
        lines.append(f"| {s.case} | " + " | ".join(cells) + f" | {result} |")
    for s in scores:
        lines += ["", f"## {s.case}", ""]
        lines += [f"- {'PASS' if ok else 'FAIL'} {label}" for _, ok, label in s.checks]
    return "\n".join(lines) + "\n"


def main() -> None:
    before = total_spend()
    cases = sorted(p.name for p in CASES.iterdir() if (p / "expected.json").exists())
    scores = [score_case(c) for c in cases]
    card = scorecard(scores)
    RESULTS.mkdir(exist_ok=True)
    (RESULTS / ("fixtures.md" if FIXTURES else "latest.md")).write_text(card, encoding="utf-8")
    print(card)
    print(f"Spend this run: ${total_spend() - before:.4f}")
    sys.exit(0 if all(s.passed for s in scores) else 1)


if __name__ == "__main__":
    main()
