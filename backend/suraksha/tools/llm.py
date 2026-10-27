"""The one door to the Meta Model API.

Every agent calls `complete()`. It routes to the model in models.yaml, logs tokens and cost,
enforces the spend limit, and in replay mode answers from recorded responses so tests and CI
never spend credits (MOD-08).
"""

import hashlib
import json
import logging
import threading
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from suraksha.config import Settings, get_settings
from suraksha.tools.models import cost_usd, load_registry, routes

log = logging.getLogger("suraksha.llm")


class ReplayMissError(RuntimeError):
    """Replay mode found no recording for this request."""


class SpendLimitError(RuntimeError):
    """The logged spend passed LLM_SPEND_LIMIT_USD."""


class LLMResult(BaseModel):
    agent: str
    model: str
    text: str
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float | None = None
    replayed: bool = False


_lock = threading.Lock()


def _request_key(payload: dict[str, Any]) -> str:
    blob = json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()
    return hashlib.sha256(blob).hexdigest()[:24]


def _ledger_path(settings: Settings) -> Path:
    return settings.llm_recordings_dir / "spend.jsonl"


def total_spend(settings: Settings | None = None) -> float:
    settings = settings or get_settings()
    path = _ledger_path(settings)
    if not path.exists():
        return 0.0
    with path.open(encoding="utf-8") as f:
        return sum(json.loads(line).get("cost_usd") or 0.0 for line in f if line.strip())


def complete(
    agent: str,
    messages: list[dict[str, Any]],
    *,
    response_schema: type[BaseModel] | None = None,
    settings: Settings | None = None,
) -> LLMResult:
    """Run one model call for `agent`. With `response_schema`, the reply is JSON for that schema."""
    settings = settings or get_settings()
    route = routes(settings.models_file)[agent]
    payload: dict[str, Any] = {"model": route.model, "messages": messages}
    if route.reasoning_effort:
        payload["reasoning_effort"] = route.reasoning_effort
    if response_schema is not None:
        payload["response_format"] = {
            "type": "json_schema",
            "json_schema": {
                "name": response_schema.__name__,
                "schema": response_schema.model_json_schema(),
                "strict": True,
            },
        }

    key = _request_key(payload)
    recording = settings.llm_recordings_dir / agent / f"{key}.json"

    if settings.llm_mode == "replay":
        if not recording.exists():
            raise ReplayMissError(
                f"no recording for {agent} request {key}; run with LLM_MODE=record"
            )
        data = json.loads(recording.read_text(encoding="utf-8"))
        return LLMResult(**{**data, "replayed": True, "cost_usd": 0.0})

    if total_spend(settings) >= settings.llm_spend_limit_usd:
        raise SpendLimitError(f"spend limit ${settings.llm_spend_limit_usd} reached")

    from openai import OpenAI  # imported here so replay-only test runs need no network client

    client = OpenAI(api_key=settings.model_api_key, base_url=settings.model_base_url)
    response = client.chat.completions.create(**payload)
    usage = response.usage
    in_tok = usage.prompt_tokens if usage else 0
    out_tok = usage.completion_tokens if usage else 0
    cached = 0
    if usage and getattr(usage, "prompt_tokens_details", None):
        cached = getattr(usage.prompt_tokens_details, "cached_tokens", 0) or 0
    cost = cost_usd(load_registry(settings.models_file), route.model, in_tok, out_tok, cached)

    result = LLMResult(
        agent=agent,
        model=route.model,
        text=response.choices[0].message.content or "",
        input_tokens=in_tok,
        output_tokens=out_tok,
        cost_usd=cost,
    )
    _log_spend(settings, key, result)
    if settings.llm_mode == "record":
        recording.parent.mkdir(parents=True, exist_ok=True)
        recording.write_text(result.model_dump_json(indent=2), encoding="utf-8")
    return result


def _log_spend(settings: Settings, key: str, result: LLMResult) -> None:
    entry = {
        "key": key,
        "agent": result.agent,
        "model": result.model,
        "input_tokens": result.input_tokens,
        "output_tokens": result.output_tokens,
        "cost_usd": result.cost_usd,
    }
    log.info("llm_call %s", json.dumps(entry))
    if result.cost_usd is None:
        log.warning("price unknown for %s; spend total undercounts", result.model)
    path = _ledger_path(settings)
    with _lock:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")
