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


# A runaway reply (seen live: 210k tokens, $0.90 in one call) is cut off here; the JSON parse
# then fails and complete_json's repair round or the caller's fallback takes over.
MAX_OUTPUT_TOKENS = 4096


def closed_schema(model: type[BaseModel]) -> dict[str, Any]:
    """The model's JSON schema with every object closed (no extra keys, every property
    required; optional fields stay nullable). Muse Spark garbles the last key of an open
    schema (e.g. "intake_complete指定") about every time; closed schemas came back clean."""

    def close(node: Any) -> None:
        if isinstance(node, dict):
            if node.get("type") == "object" and "properties" in node:
                node["additionalProperties"] = False
                node["required"] = list(node["properties"])
            for v in node.values():
                close(v)
        elif isinstance(node, list):
            for v in node:
                close(v)

    schema = model.model_json_schema()
    close(schema)
    return schema


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
    payload: dict[str, Any] = {
        "model": route.model,
        "messages": messages,
        "max_completion_tokens": MAX_OUTPUT_TOKENS,
    }
    if route.reasoning_effort:
        payload["reasoning_effort"] = route.reasoning_effort
    if response_schema is not None:
        payload["response_format"] = {
            "type": "json_schema",
            "json_schema": {
                "name": response_schema.__name__,
                "schema": closed_schema(response_schema),
                "strict": True,
            },
        }

    key = _request_key(payload)
    recording = settings.llm_recordings_dir / agent / f"{key}.json"

    # Record mode reuses an existing recording too: the model can answer the same request
    # differently, and a second answer overwriting the first breaks later replays.
    if settings.llm_mode == "replay" or (settings.llm_mode == "record" and recording.exists()):
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
    response = _create(client, payload, response_schema)
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


_json_schema_ok: bool | None = None  # learned on the first structured call


def _create(client: Any, payload: dict[str, Any], schema: type[BaseModel] | None) -> Any:
    """Call the API. If it rejects `json_schema` response formats, fall back to plain JSON
    mode with the schema spelled out in the prompt, and remember that for later calls."""
    global _json_schema_ok
    from openai import BadRequestError

    if schema is not None and _json_schema_ok is False:
        return client.chat.completions.create(**_json_mode(payload, schema))
    try:
        response = client.chat.completions.create(**payload)
        if schema is not None:
            _json_schema_ok = True
        return response
    except BadRequestError as e:
        if schema is None or "response_format" not in str(e) and "json_schema" not in str(e):
            raise
        log.warning("json_schema response_format rejected; falling back to json_object")
        _json_schema_ok = False
        return client.chat.completions.create(**_json_mode(payload, schema))


def _json_mode(payload: dict[str, Any], schema: type[BaseModel]) -> dict[str, Any]:
    spec = json.dumps(closed_schema(schema), ensure_ascii=False)
    note = {
        "role": "system",
        "content": f"Reply with one JSON object matching this schema:\n{spec}",
    }
    return {**payload, "messages": [*payload["messages"], note],
            "response_format": {"type": "json_object"}}  # fmt: skip


class AgentOutputError(RuntimeError):
    """The model's reply did not match the schema, even after one repair attempt."""


def parse_json(text: str, schema: type[BaseModel]) -> BaseModel:
    body = text.strip()
    if body.startswith("```"):
        body = body.split("\n", 1)[1].rsplit("```", 1)[0]
    try:
        return schema.model_validate_json(body)
    except ValueError:
        # Seen live: a sentence of prose before the JSON object. Try the outermost {...}.
        start, end = body.find("{"), body.rfind("}")
        if start <= 0 or end < start:
            raise
        return schema.model_validate_json(body[start : end + 1])


def complete_json[T: BaseModel](
    agent: str,
    messages: list[dict[str, Any]],
    schema: type[T],
    *,
    settings: Settings | None = None,
) -> tuple[T, LLMResult]:
    """A structured call: the reply parsed into `schema`, with one repair round if it does
    not validate (the error is shown to the model, never silently fixed by us)."""
    result = complete(agent, messages, response_schema=schema, settings=settings)
    try:
        return parse_json(result.text, schema), result
    except ValueError as err:
        repair = [*messages, {"role": "assistant", "content": result.text},
                  {"role": "user", "content": f"That reply did not match the schema: {err}. "
                   "Reply again with only the corrected JSON."}]  # fmt: skip
        result = complete(agent, repair, response_schema=schema, settings=settings)
        try:
            return parse_json(result.text, schema), result
        except ValueError as err2:
            raise AgentOutputError(f"{agent}: {err2}") from err2


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
