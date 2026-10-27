# Suraksha Setu

A multi-agent claims assistant that helps low-income Indian families claim the insurance a
death triggers (PMJJBY, PMSBY and RuPay card cover in R0). Built for the Meta Global AI
Developer Hackathon on the Meta Model API (Muse Spark, Muse Wonder, Muse Voice Transcribe).

Spec: [SRS and R0 build checklist](https://claude.ai/artifact/ARsSx4NMj8BsUvZCNCtwiB)

## Quick start (Windows, macOS, Linux)

Needs Python 3.12, [uv](https://docs.astral.sh/uv/), Node 22 and Docker Desktop (running).

```bash
cp .env.example .env          # then fill MODEL_API_KEY and VAULT_KEY
uv sync                       # backend deps
npm install --prefix console  # console deps
uv run poe dev                # Postgres + Temporal (Docker), API, mock portal, console
```

| Service | URL |
|---|---|
| API (OpenAPI docs) | http://localhost:8000/docs |
| Mock partner portal | http://localhost:8100/docs |
| Review console | http://localhost:5173 |
| Temporal UI | http://localhost:8233 |

`poe dev` applies database migrations on start. Other tasks: `uv run poe test` (database
tests need `poe infra` running), `poe migrate`, `poe lint`, `poe check-models`, `poe openapi`,
`poe down`. `uv run poe` lists them all.

## Workflows (M2)

`case_workflow` (one per death): consent → interview → documents → discovery → eligibility →
reviewer check only for weak evidence or verifier warnings → briefing → the family picks
covers → one `claim_workflow` per cover. `claim_workflow`: rules (unknown → ask the family;
not eligible → close with the reason) → documents → build and verify the packet → **human
approval of that exact packet version** (returned → rebuild) → submit → poll until paid →
a human confirms the payout (shortfall → appeal task).

Every step retries 3 times with backoff; then it becomes a reviewer task and resumes when
the task is resolved. Agents are stubs (`agents/stubs.py`) until M3–M5. Human decisions
come in through the API (`/cases/{id}/workflow/...`, `/claims/{id}/approve|return|...`,
`/tasks/{id}/resolve`), which records each one in `approval` and the audit log.

## Discovery, filing, verifier and coordinator (M5)

- **Filing** (`agents/filing/`): one packet per claim. Every form value comes from a named
  source (a document field, the family's statement, or the registry), names stay vault
  tokens, attachments follow the registry's required documents, missing ones go on the
  checklist. The model only rewrites the FIR's accident text in plain English (it keeps the
  FIR as its source); without a model the FIR text is used.
- **Verifier** (`agents/verifier/`): code decides pass/fail. Any untraced or empty required
  field, a value that differs from its document, a date of death that disagrees anywhere, a
  missing required attachment, a not-eligible rule result, a wrong amount or no nominee is a
  blocker. Muse Wonder's review adds findings capped at warnings; if it cannot run the
  verdict says so.
- **Coordinator**: the next action (request approval vs escalate) is chosen in code; the
  model writes the reviewer's summary (a summary that promises payment is discarded).
- **Discovery**: matchers find covers; for weak premium evidence the model adds a note for
  the reviewer, never an upgrade.
- `GET /claims/{id}/packet.html`: the printable packet with sources and findings beside
  each field (names revealed from the vault, audited).

## Conversation and document agents (M4)

- **Intake page** (console): chat, an "I agree" consent button, photo upload with the
  Aadhaar-masked preview beside the fields read from it, then "Finish" → the case workflow
  checks covers and shows the briefing for the family to choose claims.
- **Conversation agent** (`agents/conversation/`): condolence and the three trust facts, one
  question at a time, read-back. Code enforces what the prompt asks: nothing is kept before
  consent, a reply that promises money is rewritten once then replaced, names go straight
  to the vault, and chat is stored encrypted.
- **Screening** runs first on every message: an injection attempt gets a fixed reply and
  never reaches the agent; distress creates a call-back task and shows Tele-MANAS 14416.
- **Document agent** (`agents/document/`): reads only the masked working copy; unknown field
  names are dropped, any full Aadhaar number is re-masked, text addressed to an AI becomes a
  tamper signal, and low-confidence fields or tampering create a reviewer task.
- Without a model key everything still runs: chat answers "try again later", uploads are
  stored with extraction `pending` (`POST /cases/{id}/documents/extract-pending` retries).
- **Live check:** `uv run python scripts/eval_m4.py` (needs `MODEL_API_KEY`) scores the
  agents on the case photos and case A's chat, and records responses for replay.
  Regenerate the fake photos with `uv run python scripts/make_fake_documents.py`.

## Rules, registry and matchers (M3)

- `registry/*.yaml`: payout, filing channel, required documents, deadlines and sources per
  scheme. `rules/*.json`: one JSON-logic rule per file, effective-dated, citing its source.
- The evaluator is three-valued: a missing fact gives **unknown** plus a plain question for
  the family, never a guess. A scheme is not eligible if any rule fails (citing only those),
  unknown if any rule cannot be decided, else eligible (citing every rule).
- Matchers read passbook lines (`txn` fields: `date|description|amount|DR`) for the ₹436
  PMJJBY and ₹20 PMSBY debits and card use, and propose covers with evidence.
- `evals/cases/case_{a,b,c}`: eligible / one unknown + missing document / not eligible.
  Regenerate with `uv run python scripts/make_eval_cases.py`.
- **Rules need a human check before any real use:** see
  [docs/rules-spot-check.md](docs/rules-spot-check.md).

## Privacy plumbing (M1)

- Names, phones, account and Aadhaar numbers are Fernet-encrypted in the `vault` schema;
  every other table and every agent sees only `tok_...` tokens. Reveals are audited.
- No data write happens on a case without active consent (`403` otherwise).
- Uploaded originals are encrypted on disk. A local OCR masks Aadhaar numbers (all but the
  last 4 digits) on a working copy, and only that copy may reach the Meta API.
- `audit_event` is append-only (a database trigger blocks UPDATE/DELETE/TRUNCATE) and
  hash-chained; `GET /audit/verify` recomputes the chain.

## Layout

```
backend/suraksha/
  api/          FastAPI app (console and channel endpoints)
  contracts/    typed step I/O shared by every agent and service
  workflows/    Temporal case and claim workflows
  activities/   Temporal activities that call agents and services
  agents/       one folder per agent, each with a versioned prompt.md
  tools/        the Meta Model API client (llm.py) and model registry
  services/     rules, registry, consent, docstore, submission, scheduler
  db/           SQLAlchemy models and Alembic migrations
registry/       scheme facts (YAML)
rules/          eligibility rules as JSON-logic
mock_portal/    fake partner claim desk for R0 filing
console/        React review console
evals/          synthetic cases and recorded model responses
```

Repo rules: agents import only from `contracts/` and `tools/`; only `services/submission/`
calls outside the system; every model is declared in `models.yaml` (Meta first; a non-Meta
model needs a `reason`, CI enforces it).

## Model calls and credits

All calls go through `suraksha.tools.llm.complete()`, which logs tokens and cost to
`evals/recordings/spend.jsonl` and stops at `LLM_SPEND_LIMIT_USD`. `LLM_MODE=replay`
(the default, and what CI uses) answers from recorded responses and spends nothing;
`record` calls the API and saves responses; `live` just calls it.
