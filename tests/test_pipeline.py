"""Tests for repopilot.router, repopilot.pipeline, and repopilot.cli (offline)."""

from __future__ import annotations

from pathlib import Path

import pytest

from repopilot import cli, config, llm, pipeline, router
from repopilot.loader import inventory
from repopilot.pipeline import Result

FIXTURES = Path(__file__).parent / "fixtures"
SAMPLE_REPO = FIXTURES / "sample_repo"


def _stub_explain(question, evidence, intent, **kwargs):
    """Stand-in for the model: cite the first real evidence item."""
    items = list(evidence)
    if not items:
        return {"claims": [], "cannot_confirm": ["no evidence available"]}
    first = items[0]
    return {
        "claims": [
            {
                "statement": "A scaling mechanism is present.",
                "evidence_ids": [first.id],
                "label": "stated",
                "quote": first.text,
            }
        ],
        "cannot_confirm": ["actual capacity is unknown"],
    }


# --- Router classification ---------------------------------------------------

@pytest.mark.parametrize(
    "question,intent",
    [
        ("What does this project do?", "overview"),
        ("How does this system handle a large number of users?", "scaling"),
        ("How does it scale to millions?", "scaling"),
        ("Why is authentication used here?", "why"),
        ("Why was this method chosen instead of sessions?", "why"),
        ("Walk me through the login flow", "trace"),
        ("Where is the config file?", "general"),
    ],
)
def test_classify(question, intent):
    assert router.classify(question) == intent


def test_scaling_gather_is_capped_and_reports_missing():
    route = router.gather(SAMPLE_REPO, inventory(SAMPLE_REPO), "How does it scale?")

    assert route.intent == "scaling"
    assert 1 <= len(route.evidence) <= config.MAX_EVIDENCE
    assert set(route.missing) == {"db_scaling", "stateless_auth", "rate_limiting"}
    assert route.evidence[0].tag in config.SCALING_TAGS


def test_overview_gather_returns_verifiable_evidence():
    route = router.gather(SAMPLE_REPO, inventory(SAMPLE_REPO), "Give me an overview")

    assert route.intent == "overview"
    assert route.evidence
    assert all(item.path for item in route.evidence)


# --- Pipeline acceptance (scaling) ------------------------------------------

def test_scaling_answer_is_verified_with_not_found_and_cannot_confirm(monkeypatch):
    monkeypatch.setattr(pipeline.llm, "explain", _stub_explain)

    result = pipeline.answer_path(
        SAMPLE_REPO, "How does this system handle a large number of users?"
    )

    assert result.intent == "scaling"
    assert result.counts["kept"] >= 1
    assert result.counts["dropped"] == 0
    assert result.claims[0]["label"] == "stated"
    assert result.claims[0]["citations"], "kept claims must carry a file:line citation"

    # Missing categories reported as Not found.
    assert set(result.missing) == {"db_scaling", "stateless_auth", "rate_limiting"}

    # Always ends with a "Cannot confirm" section for scaling.
    assert result.cannot_confirm


def test_scaling_answer_adds_default_cannot_confirm_when_model_forgets(monkeypatch):
    def silent_model(question, evidence, intent, **kwargs):
        return {"claims": [], "cannot_confirm": []}

    monkeypatch.setattr(pipeline.llm, "explain", silent_model)
    result = pipeline.answer_path(SAMPLE_REPO, "How does it scale?")

    assert result.cannot_confirm == [pipeline.DEFAULT_CANNOT_CONFIRM]


def test_inject_fake_is_dropped(monkeypatch):
    monkeypatch.setattr(pipeline.llm, "explain", _stub_explain)

    result = pipeline.answer_path(SAMPLE_REPO, "How does it scale?", inject_fake=True)

    assert result.counts["dropped"] >= 1


# --- Error handling ----------------------------------------------------------

def test_invalid_url_returns_error():
    result = pipeline.answer("https://gitlab.com/octocat/Hello-World", "q", use_cache=False)

    assert not result.ok
    assert "github" in result.error.lower()


def test_llm_error_is_friendly(monkeypatch):
    def boom(question, evidence, intent, **kwargs):
        raise llm.LLMError("Could not reach the local Ollama server.")

    monkeypatch.setattr(pipeline.llm, "explain", boom)
    result = pipeline.answer_path(SAMPLE_REPO, "Give me an overview")

    assert not result.ok
    assert "ollama" in result.error.lower()


def test_result_round_trips_through_dict():
    original = Result(
        repo_url="u", question="q", intent="scaling",
        claims=[{"statement": "x", "label": "stated"}],
        cannot_confirm=["c"], missing=["m"],
        counts={"kept": 1, "flagged": 0, "dropped": 0},
        badge="1 verified, 0 flagged, 0 dropped",
    )

    assert Result.from_dict(original.to_dict()) == original


# --- CLI ---------------------------------------------------------------------

def _fake_result() -> Result:
    return Result(
        repo_url="https://github.com/a/b",
        question="How does it scale?",
        intent="scaling",
        claims=[{"statement": "Redis is used.", "label": "stated", "citations": ["requirements.txt:1"], "quote": "redis", "status": "kept"}],
        cannot_confirm=["actual capacity is unknown"],
        missing=["db_scaling"],
        counts={"kept": 1, "flagged": 0, "dropped": 0},
        badge="1 verified, 0 flagged, 0 dropped",
    )


def test_cli_prints_sections(monkeypatch, capsys):
    monkeypatch.setattr(cli.pipeline, "answer", lambda *a, **k: _fake_result())

    code = cli.main(["https://github.com/a/b", "How does it scale?"])
    out = capsys.readouterr().out

    assert code == 0
    assert "Intent: scaling" in out
    assert "Redis is used." in out
    assert "requirements.txt:1" in out
    assert "Not found: db_scaling" in out
    assert "Cannot confirm from code" in out
    assert "1 verified, 0 flagged, 0 dropped" in out


def test_cli_shows_dropped_claims(monkeypatch, capsys):
    result = _fake_result()
    result.dropped = [{"statement": "Fabricated claim.", "reason": "fabricated or unknown evidence id(s): E999"}]
    monkeypatch.setattr(cli.pipeline, "answer", lambda *a, **k: result)

    cli.main(["https://github.com/a/b", "q"])
    out = capsys.readouterr().out

    assert "Dropped (failed verification):" in out
    assert "E999" in out


def test_cli_returns_nonzero_on_error(monkeypatch, capsys):
    monkeypatch.setattr(cli.pipeline, "answer", lambda *a, **k: Result("u", "q", "", error="bad url"))

    assert cli.main(["u", "q"]) == 1
    assert "Could not answer" in capsys.readouterr().out
