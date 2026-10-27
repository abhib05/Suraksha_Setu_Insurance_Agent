import json

import pytest

from suraksha.config import Settings
from suraksha.tools import llm


def _settings(tmp_path, mode="replay"):
    return Settings(llm_mode=mode, llm_recordings_dir=tmp_path, model_api_key="")


def test_replay_miss_raises(tmp_path):
    with pytest.raises(llm.ReplayMissError):
        llm.complete(
            "conversation", [{"role": "user", "content": "hi"}], settings=_settings(tmp_path)
        )


def test_replay_returns_recording_without_cost(tmp_path):
    messages = [{"role": "user", "content": "hi"}]
    payload = {"model": "muse-spark-1.3", "messages": messages, "reasoning_effort": "low"}
    key = llm._request_key(payload)
    (tmp_path / "conversation").mkdir()
    (tmp_path / "conversation" / f"{key}.json").write_text(
        json.dumps(
            {
                "agent": "conversation",
                "model": "muse-spark-1.3",
                "text": "Namaste",
                "input_tokens": 5,
                "output_tokens": 2,
                "cost_usd": 0.00001,
            }
        )
    )
    result = llm.complete("conversation", messages, settings=_settings(tmp_path))
    assert result.text == "Namaste" and result.replayed and result.cost_usd == 0.0


def test_spend_limit_blocks_live_calls(tmp_path):
    (tmp_path / "spend.jsonl").write_text(json.dumps({"cost_usd": 200.0}) + "\n")
    with pytest.raises(llm.SpendLimitError):
        llm.complete(
            "conversation",
            [{"role": "user", "content": "hi"}],
            settings=_settings(tmp_path, mode="live"),
        )
