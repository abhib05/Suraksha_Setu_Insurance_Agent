"""CI gate: models.yaml must follow the model policy. Exits 1 on any violation."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from suraksha.tools.models import load_registry, policy_violations  # noqa: E402


def main() -> int:
    path = Path(__file__).resolve().parents[1] / "models.yaml"
    problems = policy_violations(load_registry(path))
    for p in problems:
        print(f"models.yaml: {p}")
    if not problems:
        print("models.yaml: OK")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
