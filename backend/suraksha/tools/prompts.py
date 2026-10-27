"""Versioned prompts: each agent's prompt.md starts with a `Version: N` line, which every
audit event records next to the model ID."""

import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

AGENTS_DIR = Path(__file__).resolve().parents[1] / "agents"


@dataclass(frozen=True)
class Prompt:
    agent: str
    version: str
    text: str


@lru_cache
def load_prompt(agent: str) -> Prompt:
    raw = (AGENTS_DIR / agent / "prompt.md").read_text(encoding="utf-8")
    m = re.search(r"^Version:\s*(\S+)", raw, re.M)
    if not m:
        raise ValueError(f"{agent}/prompt.md has no 'Version:' line")
    return Prompt(agent, m.group(1), raw)
