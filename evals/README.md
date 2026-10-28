# Evaluation cases

Each folder under `cases/` holds `facts.json` (what the interview should capture),
`documents/` (fake document photos) and `expected.json` (covers, rule results, packet
checklist). All data is invented; no real person's data enters the repo.

- `case_a`: road accident, eligible for PMJJBY, PMSBY and RuPay
- `case_b`: a required document is missing
- `case_c`: not eligible, with a plain-language reason

`recordings/` holds recorded model responses so tests replay them instead of spending credits.

## Scoring (`uv run poe eval`)

`scripts/eval.py` runs each case through the pipeline (photos -> masked -> document agent ->
discovery -> rules -> filing -> verifier) and scores covers, rule results, payouts, missing
documents and packets against `expected.json`. It replays recorded model calls, so it costs
nothing and runs in CI; `poe eval-record` records requests that changed. `--fixtures` uses
`documents.json` instead of the photos (no model). The scorecard lands in
`results/latest.md`.
