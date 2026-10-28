"""Voice input (M7 stretch): Muse Voice Transcribe behind the same replay/spend rules as the
chat models, and a push-to-talk endpoint that needs consent and never keeps the audio."""

import hashlib
import io
import json
import wave

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from suraksha.api import intake as intake_api
from suraksha.api.main import app
from suraksha.config import Settings
from suraksha.db.base import session_scope
from suraksha.tools import llm


def wav(seconds=1.0, rate=16000, channels=1) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"\x01\x00" * int(seconds * rate) * channels)
    return buf.getvalue()


def _settings(tmp_path, mode="replay"):
    return Settings(llm_mode=mode, llm_recordings_dir=tmp_path, model_api_key="test")


def _key(audio: bytes, languages=None) -> str:
    request = {"model": "muse-voice-transcribe-1.0", "audioEncoding": "WAV",
               "mode": "PUSH_TO_TALK"}
    if languages:
        request["languageBias"] = languages
    return llm._request_key({**request, "audio_sha256": hashlib.sha256(audio).hexdigest()})


def test_transcribe_replays_by_audio_hash(tmp_path):
    audio = wav()
    (tmp_path / "transcribe").mkdir()
    (tmp_path / "transcribe" / f"{_key(audio, ['Hindi'])}.json").write_text(json.dumps(
        {"text": "मेरे पति की सड़क दुर्घटना में मौत हुई", "audio_ms": 1000,
         "model": "muse-voice-transcribe-1.0", "cost_usd": 0.00005}))  # fmt: skip
    out = llm.transcribe(audio, languages=["Hindi"], settings=_settings(tmp_path))
    assert out.text.startswith("मेरे") and out.replayed and out.cost_usd == 0.0
    with pytest.raises(llm.ReplayMissError):  # a different language bias is another request
        llm.transcribe(audio, languages=["Kannada"], settings=_settings(tmp_path))


def test_transcribe_live_logs_cost_and_records_text_only(tmp_path, monkeypatch):
    import httpx

    sent = {}

    def fake_post(url, headers, files, timeout):
        sent.update(url=url, request=json.loads(files["request"][1]))
        return httpx.Response(200, json={"sessionId": "s", "transcript": " Namaskara ",
                                         "audioDurationMs": 3_600_000, "turns": []},
                              request=httpx.Request("POST", url))  # fmt: skip

    monkeypatch.setattr(httpx, "post", fake_post)
    audio = wav()
    out = llm.transcribe(audio, languages=["Kannada"], settings=_settings(tmp_path, "record"))
    assert sent["url"].endswith("/asr/transcribe")
    assert sent["request"]["languageBias"] == ["Kannada"]
    assert "keywords" not in sent["request"]
    assert out.text == "Namaskara" and out.cost_usd == pytest.approx(0.18)  # one audio hour
    assert llm.total_spend(_settings(tmp_path)) == pytest.approx(0.18)
    saved = (tmp_path / "transcribe" / f"{_key(audio, ['Kannada'])}.json").read_bytes()
    assert audio[44:100] not in saved  # the recording keeps the text, never the audio


@pytest.fixture
def case_with_consent(migrated_db):
    client = TestClient(app)
    case_id = client.post("/cases", json={}).json()["case_id"]
    return client, case_id


def test_voice_needs_consent(case_with_consent):
    client, case_id = case_with_consent
    r = client.post(f"/cases/{case_id}/voice", files={"audio": ("v.wav", wav(), "audio/wav")})
    assert r.status_code == 403


@pytest.mark.parametrize("audio", [b"not a wav", wav(channels=2), wav(rate=44100), wav(130)],
                         ids=["not-wav", "stereo", "44khz", "too-long"])  # fmt: skip
def test_voice_rejects_audio_the_model_cannot_take(case_with_consent, audio):
    client, case_id = case_with_consent
    client.post(f"/cases/{case_id}/consent", json={"given_by_name": "Sunita Sample"})
    r = client.post(f"/cases/{case_id}/voice", files={"audio": ("v.wav", audio, "audio/wav")})
    assert r.status_code == 422


def test_voice_returns_text_and_audits_only_hashes(case_with_consent, monkeypatch):
    client, case_id = case_with_consent
    client.post(f"/cases/{case_id}/consent", json={"given_by_name": "Sunita Sample"})
    seen = {}

    def fake(data, languages=None):
        seen["languages"] = languages
        return llm.Transcript(text="Mere pati Ramesh Sample the", audio_ms=1000,
                              model="muse-voice-transcribe-1.0")  # fmt: skip

    monkeypatch.setattr(intake_api, "transcribe", fake)
    r = client.post(f"/cases/{case_id}/voice", data={"language": "hindi"},
                    files={"audio": ("v.wav", wav(), "audio/wav")})  # fmt: skip
    assert r.status_code == 200 and r.json() == {"text": "Mere pati Ramesh Sample the",
                                                  "audio_ms": 1000}  # fmt: skip
    assert seen["languages"] == ["Hindi"]
    with session_scope() as s:
        rows = s.execute(text("SELECT row_to_json(a)::text FROM audit_event a "
                              "WHERE action = 'voice.transcribed'")).scalars().all()  # fmt: skip
    assert len(rows) == 1 and "Ramesh" not in rows[0] and '"output_hash"' in rows[0]


def test_voice_says_so_when_the_model_is_unavailable(case_with_consent, monkeypatch):
    client, case_id = case_with_consent
    client.post(f"/cases/{case_id}/consent", json={"given_by_name": "Sunita Sample"})

    def miss(data, languages=None):
        raise llm.ReplayMissError("no recording")

    monkeypatch.setattr(intake_api, "transcribe", miss)
    r = client.post(f"/cases/{case_id}/voice", files={"audio": ("v.wav", wav(), "audio/wav")})
    assert r.status_code == 503 and "type instead" in r.json()["detail"]
