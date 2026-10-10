"""Tests for repopilot.llm.

Offline by default: the model is faked. A real end-to-end check runs only when
REPOPILOT_RUN_OLLAMA_TESTS=1 and Ollama is serving qwen2.5-coder:3b.
"""

from __future__ import annotations

import os
from types import SimpleNamespace

import ollama
import pytest

from repopilot import config
from repopilot.evidence import Evidence, EvidenceCollection
from repopilot.llm import LLMError, build_prompt, explain


def _evidence() -> EvidenceCollection:
    return EvidenceCollection(
        [
            Evidence("", "dependency", "requirements.txt", 1, 1, "redis==5.0.0", tag="caching"),
            Evidence("", "config", "docker-compose.yml", 5, 5, "replicas: 3", tag="horizontal_scaling"),
        ]
    )


class FakeClient:
    """Returns queued contents, one per chat call."""

    def __init__(self, *contents: str) -> None:
        self._contents = list(contents)
        self.calls: list[dict] = []

    def chat(self, **kwargs):
        self.calls.append(kwargs)
        content = self._contents.pop(0) if self._contents else ""
        return SimpleNamespace(message=SimpleNamespace(content=content))


class RaisingClient:
    def __init__(self, exc: Exception) -> None:
        self._exc = exc

    def chat(self, **kwargs):
        raise self._exc


# --- Prompt building ---------------------------------------------------------

def test_build_prompt_mentions_ids_and_untrusted_data():
    system, user = build_prompt("How does it scale?", _evidence(), "scaling")

    assert "[E1]" in user and "[E2]" in user
    assert "requirements.txt:1" in user
    assert "untrusted" in user.lower()
    assert "JSON" in user
    assert "JSON only" in system


def test_build_prompt_caps_evidence_at_max():
    many = EvidenceCollection(
        [Evidence("", "code", f"f{i}.py", 1, 1, "x") for i in range(config.MAX_EVIDENCE + 4)]
    )

    _, user = build_prompt("q", many, "general")

    assert f"[E{config.MAX_EVIDENCE}]" in user
    assert f"[E{config.MAX_EVIDENCE + 1}]" not in user


# --- explain() behaviour -----------------------------------------------------

def test_explain_returns_parsed_dict():
    client = FakeClient('{"claims": [], "cannot_confirm": []}')

    result = explain("q", _evidence(), "scaling", client=client)

    assert result == {"claims": [], "cannot_confirm": []}
    assert client.calls[0]["format"] == "json"
    assert client.calls[0]["model"] == config.MODEL_NAME
    assert client.calls[0]["options"]["temperature"] == config.TEMPERATURE


def test_explain_accepts_dict_style_response():
    client = FakeClient()
    client.chat = lambda **kwargs: {"message": {"content": '{"claims": []}'}}  # type: ignore[assignment]

    assert explain("q", _evidence(), "scaling", client=client) == {"claims": []}


def test_explain_recovers_from_fenced_json():
    client = FakeClient('```json\n{"claims": []}\n```')

    assert explain("q", _evidence(), "scaling", client=client) == {"claims": []}


def test_explain_retries_once_then_succeeds():
    client = FakeClient("this is not json", '{"claims": [{"statement": "ok"}]}')

    result = explain("q", _evidence(), "scaling", client=client)

    assert result["claims"][0]["statement"] == "ok"
    assert len(client.calls) == 2


def test_explain_fails_gracefully_after_two_invalid_attempts():
    client = FakeClient("nope", "still nope")

    with pytest.raises(LLMError) as excinfo:
        explain("q", _evidence(), "scaling", client=client)

    assert "valid JSON" in str(excinfo.value)
    assert len(client.calls) == 2


def test_explain_wraps_model_errors():
    client = RaisingClient(ollama.ResponseError("model 'x' not found"))

    with pytest.raises(LLMError) as excinfo:
        explain("q", _evidence(), "scaling", client=client)

    assert "could not answer" in str(excinfo.value).lower()


def test_explain_reports_unreachable_ollama():
    # Point the real client at a closed local port: no external network needed.
    unreachable = ollama.Client(host="http://127.0.0.1:9")

    with pytest.raises(LLMError) as excinfo:
        explain("q", _evidence(), "scaling", client=unreachable)

    assert "ollama" in str(excinfo.value).lower()


# --- Opt-in real model -------------------------------------------------------

@pytest.mark.skipif(
    not os.environ.get("REPOPILOT_RUN_OLLAMA_TESTS"),
    reason="set REPOPILOT_RUN_OLLAMA_TESTS=1 to run against the real local model",
)
def test_explain_real_ollama_returns_json():
    result = explain("How does this system cache data?", _evidence(), "scaling")

    assert isinstance(result, dict)
    assert "claims" in result
