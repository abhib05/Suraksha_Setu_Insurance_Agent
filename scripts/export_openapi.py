"""Write the API's OpenAPI spec to console/openapi.json for the typed console client."""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from suraksha.api.main import app  # noqa: E402

out = ROOT / "console" / "openapi.json"
out.write_text(json.dumps(app.openapi(), indent=2), encoding="utf-8")
print(f"wrote {out.relative_to(ROOT)}")
