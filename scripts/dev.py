"""`uv run poe dev`: start Postgres + Temporal in Docker, then API, mock portal and console.

Ctrl+C stops the three app processes; containers keep running (`uv run poe down` stops them).
"""

import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
# On Windows `npm` resolves to a shell script that CreateProcess cannot run; use npm.cmd.
NPM = shutil.which("npm.cmd" if sys.platform == "win32" else "npm") or "npm"

# Watch only source folders; watching .venv makes uvicorn reload on every package file.
UVICORN = [sys.executable, "-m", "uvicorn", "--reload"]
PROCESSES = {
    "api": [
        *UVICORN,
        "suraksha.api.main:app",
        "--port",
        "8000",
        "--app-dir",
        "backend",
        "--reload-dir",
        "backend",
    ],
    "portal": [*UVICORN, "mock_portal.app:app", "--port", "8100", "--reload-dir", "mock_portal"],
    "console": [NPM, "run", "dev", "--prefix", "console"],
}


def wait_for_postgres(timeout: float = 60) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        ok = (
            subprocess.run(
                ["docker", "compose", "exec", "-T", "postgres", "pg_isready", "-U", "suraksha"],
                cwd=ROOT,
                capture_output=True,
            ).returncode
            == 0
        )
        if ok:
            return
        time.sleep(1)
    sys.exit("Postgres did not become ready; check `docker compose logs postgres`")


def main() -> None:
    if subprocess.run(["docker", "info"], capture_output=True).returncode != 0:
        sys.exit("Docker is not running. Start Docker Desktop and try again.")
    subprocess.run(["docker", "compose", "up", "-d"], cwd=ROOT, check=True)
    wait_for_postgres()
    subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], cwd=ROOT, check=True)

    procs: dict[str, subprocess.Popen] = {}
    try:
        for name, cmd in PROCESSES.items():
            procs[name] = subprocess.Popen(cmd, cwd=ROOT)
        print(
            "\nAPI       http://localhost:8000/docs"
            "\nPortal    http://localhost:8100/docs"
            "\nConsole   http://localhost:5173"
            "\nTemporal  http://localhost:8233\n"
        )
        while all(p.poll() is None for p in procs.values()):
            time.sleep(1)
        dead = [n for n, p in procs.items() if p.poll() is not None]
        print(f"stopped: {', '.join(dead)}")
    except KeyboardInterrupt:
        pass
    finally:
        for p in procs.values():
            if p.poll() is None:
                _kill_tree(p)


def _kill_tree(p: subprocess.Popen) -> None:
    """uvicorn --reload and npm spawn children; stop the whole tree, not just the parent."""
    if sys.platform == "win32":
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(p.pid)], capture_output=True)
    else:
        p.terminate()


if __name__ == "__main__":
    main()
