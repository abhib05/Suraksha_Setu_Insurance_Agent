from pathlib import Path

from suraksha.tools.models import cost_usd, load_registry, policy_violations

ROOT = Path(__file__).resolve().parents[1]


def test_repo_models_yaml_is_compliant():
    assert policy_violations(load_registry(ROOT / "models.yaml")) == []


def test_policy_catches_violations():
    registry = {
        "models": {
            "muse-spark-1.3-contributor": {"provider": "meta", "approved": True},
            "bge-m3": {"provider": "baai", "approved": True},
            "draft-model": {"provider": "meta", "approved": False},
        },
        "agents": {"filing": {"model": "not-declared"}},
    }
    problems = " | ".join(policy_violations(registry))
    assert "contributor" in problems
    assert "bge-m3: non-Meta model needs a `reason`" in problems
    assert "draft-model: not approved" in problems
    assert "not-declared" in problems


def test_cost_uses_cached_rate_and_handles_unknown_price():
    registry = load_registry(ROOT / "models.yaml")
    # 1M fresh input + 1M output on Muse Spark = 1.25 + 4.25
    assert cost_usd(registry, "muse-spark-1.3", 1_000_000, 1_000_000) == 5.50
    assert cost_usd(registry, "muse-spark-1.3", 1_000_000, 0, cached=1_000_000) == 0.15
    assert cost_usd(registry, "muse-spark-1.2", 10, 10) is None
