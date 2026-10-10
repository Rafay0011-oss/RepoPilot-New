"""Tests for repopilot.ui (pure helpers) and the Streamlit app wiring (offline)."""

from __future__ import annotations

import importlib
from pathlib import Path

import pytest

from repopilot import pipeline, ui
from repopilot.pipeline import Result

FIXTURES = Path(__file__).parent / "fixtures"
SAMPLE_REPO = FIXTURES / "sample_repo"


def _result() -> Result:
    return Result(
        repo_url="https://github.com/a/b",
        question="How does it scale?",
        intent="scaling",
        evidence=[
            {
                "id": "E1", "kind": "dependency", "path": "requirements.txt",
                "start_line": 1, "end_line": 1, "text": "redis==5.0.1",
                "commit": None, "tag": "caching",
                "location": "requirements.txt:1", "block": "   1 | redis==5.0.1",
            }
        ],
        claims=[
            {
                "statement": "Redis is used for caching.", "label": "stated",
                "status": "kept", "reason": None, "evidence_ids": ["E1"],
                "citations": ["requirements.txt:1"], "quote": "redis==5.0.1",
            },
            {
                "statement": "It scales to 5 million users.", "label": "stated",
                "status": "flagged", "reason": "quote not found in the cited evidence",
                "evidence_ids": ["E1"], "citations": ["requirements.txt:1"],
                "quote": "5 million users",
            },
        ],
        dropped=[
            {
                "statement": "Redis shards across 12 nodes.", "label": "stated",
                "status": "dropped", "reason": "fabricated or unknown evidence id(s): E999",
                "evidence_ids": ["E999"], "citations": [], "quote": None,
            }
        ],
        missing=["db_scaling", "stateless_auth"],
        counts={"kept": 1, "flagged": 1, "dropped": 1},
        badge="1 verified, 1 flagged, 1 dropped",
    )


# --- Pure helpers ------------------------------------------------------------

def test_verifier_badge():
    assert ui.verifier_badge({"kept": 5, "flagged": 1, "dropped": 0}) == "5 verified, 1 flagged, 0 dropped"
    assert ui.verifier_badge(None) == "0 verified, 0 flagged, 0 dropped"


@pytest.mark.parametrize(
    "evidence,expected",
    [
        ({"kind": "code", "path": "a.py", "start_line": 3, "end_line": 3}, "a.py:3"),
        ({"kind": "code", "path": "a.py", "start_line": 3, "end_line": 5}, "a.py:3-5"),
        ({"kind": "commit", "commit": "abcdef1234567890"}, "commit abcdef123456"),
        ({"kind": "doc", "path": "README.md", "start_line": None}, "README.md"),
    ],
)
def test_citation_label(evidence, expected):
    assert ui.citation_label(evidence) == expected


def test_citation_block_prefers_precomputed_block():
    assert ui.citation_block({"block": "   1 | x", "text": "x", "start_line": 1}) == "   1 | x"


def test_citation_block_numbers_lines_from_text():
    block = ui.citation_block({"kind": "code", "text": "a\nb", "start_line": 10})
    assert block == "  10 | a\n  11 | b"


# --- build_answer_view -------------------------------------------------------

def test_build_answer_view_from_result():
    view = ui.build_answer_view(_result())

    assert view.intent == "scaling"
    assert view.badge == "1 verified, 1 flagged, 1 dropped"
    assert [c.label for c in view.claims] == ["stated", "stated"]
    assert view.claims[0].citations[0].label == "requirements.txt:1"
    assert "redis==5.0.1" in view.claims[0].citations[0].block
    assert view.claims[1].warning == "quote not found in the cited evidence"
    assert view.dropped[0].statement.startswith("Redis shards")
    assert view.dropped[0].warning == "fabricated or unknown evidence id(s): E999"
    assert view.missing == ["db_scaling", "stateless_auth"]


def test_build_answer_view_from_dict():
    view = ui.build_answer_view(_result().to_dict())

    assert view.badge == "1 verified, 1 flagged, 1 dropped"
    assert view.claims[0].statement == "Redis is used for caching."


def test_build_answer_view_error():
    view = ui.build_answer_view(Result("u", "q", "", error="Only public GitHub URLs are supported."))

    assert view.error == "Only public GitHub URLs are supported."
    assert view.claims == []


def test_build_answer_view_skips_unknown_evidence_ids():
    result = _result()
    result.claims[0]["evidence_ids"] = ["E404"]

    view = ui.build_answer_view(result)

    assert view.claims[0].citations == []


# --- Integration with the real pipeline (offline, stubbed model) -------------

def test_view_from_real_pipeline_result(monkeypatch):
    def stub(question, evidence, intent, **kwargs):
        first = list(evidence)[0]
        return {
            "claims": [
                {"statement": "A mechanism exists.", "evidence_ids": [first.id],
                 "label": "stated", "quote": first.text}
            ],
            "cannot_confirm": ["capacity"],
        }

    monkeypatch.setattr(pipeline.llm, "explain", stub)
    result = pipeline.answer_path(SAMPLE_REPO, "How does it scale?")

    view = ui.build_answer_view(result)

    assert view.claims and view.claims[0].citations
    citation = view.claims[0].citations[0]
    assert citation.label and citation.block  # block carries the real cited lines


# --- Streamlit app wiring ----------------------------------------------------

def test_app_module_exposes_entrypoints():
    pytest.importorskip("streamlit")
    app = importlib.import_module("app")

    assert callable(app.main)
    assert callable(app.render)


def test_app_full_flow_renders_answer(monkeypatch):
    pytest.importorskip("streamlit")
    from streamlit.testing.v1 import AppTest

    monkeypatch.setattr(pipeline, "answer", lambda *a, **k: _result())

    app_path = Path(__file__).resolve().parent.parent / "app.py"
    at = AppTest.from_file(str(app_path), default_timeout=30)
    at.run()
    assert not at.exception

    at.text_input[0].set_value("https://github.com/a/b")
    at.text_input[1].set_value("How does it scale?")
    at.button[0].click().run()

    assert not at.exception
    rendered = " ".join(block.value for block in at.markdown)
    assert "Redis is used for caching." in rendered
    assert "Dropped (failed verification)" in rendered
    assert "requirements.txt:1" in [expander.label for expander in at.expander]
    assert any("redis==5.0.1" in code.value for code in at.code)
    assert "db_scaling" in " ".join(w.value for w in at.warning)
