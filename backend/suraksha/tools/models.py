"""Loads models.yaml and checks it against the model policy (SRS 2.7)."""

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml


@dataclass(frozen=True)
class AgentRoute:
    agent: str
    model: str
    reasoning_effort: str | None
    fallback: str | None


def load_registry(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as f:
        return yaml.safe_load(f)


def policy_violations(registry: dict[str, Any]) -> list[str]:
    """Every rule the model policy imposes on models.yaml. Empty list means compliant."""
    problems: list[str] = []
    models: dict[str, dict] = registry.get("models") or {}
    agents: dict[str, dict] = registry.get("agents") or {}

    for model_id, spec in models.items():
        if "contributor" in model_id.lower():
            problems.append(f"{model_id}: -contributor models are banned (Meta trains on content)")
        if spec.get("approved") is not True:
            problems.append(f"{model_id}: not approved")
        if spec.get("provider") != "meta" and not str(spec.get("reason") or "").strip():
            problems.append(f"{model_id}: non-Meta model needs a `reason` naming the gap it fills")

    for agent, route in agents.items():
        for key in ("model", "fallback"):
            ref = route.get(key)
            if ref and ref not in models:
                problems.append(f"agent {agent}: {key} {ref!r} is not declared under models")
    return problems


@lru_cache
def routes(path: Path) -> dict[str, AgentRoute]:
    registry = load_registry(path)
    return {
        name: AgentRoute(name, r["model"], r.get("reasoning_effort"), r.get("fallback"))
        for name, r in registry["agents"].items()
    }


def cost_usd(
    registry: dict[str, Any], model_id: str, input_tokens: int, output_tokens: int, cached: int = 0
) -> float | None:
    """Cost of one call, or None when the price is not known."""
    price = (registry["models"].get(model_id) or {}).get("price_usd_per_1m")
    if not price:
        return None
    cached_rate = price.get("cached_input", price["input"])
    fresh = max(input_tokens - cached, 0)
    return (fresh * price["input"] + cached * cached_rate + output_tokens * price["output"]) / 1e6
