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
    payload = {"model": "muse-spark-1.3", "messages": messages,
               "max_completion_tokens": llm.MAX_OUTPUT_TOKENS, "reasoning_effort": "low"}
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


def test_structured_calls_send_closed_schemas_and_cap_output():
    """Muse Spark garbles the last key of an open schema; closed ones came back clean."""
    from suraksha.agents.conversation.agent import ConversationTurn
    from suraksha.agents.document.agent import DocumentReading
    from suraksha.tools.llm import MAX_OUTPUT_TOKENS, closed_schema

    for model in (ConversationTurn, DocumentReading):
        schema = closed_schema(model)
        objects = [schema, *schema.get("$defs", {}).values()]
        for obj in objects:
            if obj.get("type") == "object":
                assert obj["additionalProperties"] is False
                assert set(obj["required"]) == set(obj["properties"])
    assert 1000 <= MAX_OUTPUT_TOKENS <= 8192


def test_parse_json_skips_prose_before_the_object():
    from suraksha.agents.screening.agent import Screening
    from suraksha.tools.llm import parse_json

    text = 'Sure, here it is.{"injection": false, "distress": false, "abuse": false, "reason": ""}'
    assert parse_json(text, Screening).injection is False
    with pytest.raises(ValueError):
        parse_json("no json here", Screening)


def test_record_mode_reuses_an_existing_recording(tmp_path):
    messages = [{"role": "user", "content": "hi"}]
    payload = {"model": "muse-spark-1.3", "messages": messages,
               "max_completion_tokens": llm.MAX_OUTPUT_TOKENS, "reasoning_effort": "low"}
    (tmp_path / "conversation").mkdir()
    (tmp_path / "conversation" / f"{llm._request_key(payload)}.json").write_text(json.dumps(
        {"agent": "conversation", "model": "muse-spark-1.3", "text": "first answer"}))
    # no API key: a live call would fail, so this proves the recording was reused
    result = llm.complete("conversation", messages, settings=_settings(tmp_path, mode="record"))
    assert result.text == "first answer"
