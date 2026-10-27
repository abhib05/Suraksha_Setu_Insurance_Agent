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

Other tasks: `uv run poe test`, `poe lint`, `poe check-models`, `poe openapi`, `poe down`.
`uv run poe` lists them all.

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
