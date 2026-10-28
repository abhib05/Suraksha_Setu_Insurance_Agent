"""`uv run poe demo-reset`: an empty app for a demo run. Stops every running workflow, drops
and re-creates the database schemas (the audit log refuses deletes, so the schemas go
whole), and clears stored documents and the mock portal's filings.

Destroys all local case data. Model recordings are kept.
"""

import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PSQL = ["docker", "compose", "exec", "-T", "postgres", "psql", "-U", "suraksha", "-d", "suraksha",
        "-v", "ON_ERROR_STOP=1", "-c"]  # fmt: skip


def run(cmd: list[str], check: bool = True) -> None:
    subprocess.run(cmd, cwd=ROOT, check=check)


def main() -> None:
    if "--yes" not in sys.argv and input("Delete every local case? [y/N] ").lower() != "y":
        sys.exit("Nothing changed.")
    # Not checked: there may be no running workflows to stop.
    run(["docker", "compose", "exec", "-T", "temporal", "temporal", "workflow", "terminate",
         "--query", "ExecutionStatus='Running'", "--reason", "demo reset", "--yes"],
        check=False)  # fmt: skip
    run([*PSQL, "DROP SCHEMA IF EXISTS vault CASCADE; DROP SCHEMA public CASCADE; "
                "CREATE SCHEMA public;"])  # fmt: skip
    run([sys.executable, "-m", "alembic", "upgrade", "head"])
    shutil.rmtree(ROOT / "data" / "documents", ignore_errors=True)
    (ROOT / "data" / "mock_portal.json").unlink(missing_ok=True)
    print("Reset done. Reload the console and press New case.")


if __name__ == "__main__":
    main()
