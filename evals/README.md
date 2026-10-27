# Evaluation cases

Each folder under `cases/` holds `facts.json` (what the interview should capture),
`documents/` (fake document photos) and `expected.json` (covers, rule results, packet
checklist). All data is invented; no real person's data enters the repo.

- `case_a`: road accident, eligible for PMJJBY, PMSBY and RuPay
- `case_b`: a required document is missing
- `case_c`: not eligible, with a plain-language reason

`recordings/` holds recorded model responses so tests replay them instead of spending credits.
