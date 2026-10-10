"""Tests for repopilot.verifier (deterministic, offline)."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from repopilot.evidence import Evidence, EvidenceCollection
from repopilot.verifier import inject_fake_claim, verify_claims

needs_git = pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    (tmp_path / "requirements.txt").write_text("redis==5.0.0\ncelery==5.3.6\n", encoding="utf-8")
    (tmp_path / "app.py").write_text("print('hi')\nprint('bye')\n", encoding="utf-8")
    return tmp_path


def ev(
    evidence_id: str,
    kind: str = "dependency",
    path: str | None = "requirements.txt",
    start: int | None = 1,
    end: int | None = 1,
    text: str = "redis==5.0.0",
    commit: str | None = None,
    tag: str | None = "caching",
) -> Evidence:
    return Evidence(evidence_id, kind, path, start, end, text, commit, tag)


def claim(ids, label="stated", quote="redis==5.0.0", statement="Redis is used to cache data."):
    return {"statement": statement, "evidence_ids": ids, "label": label, "quote": quote}


# --- Acceptance tests --------------------------------------------------------

def test_correct_claim_is_kept(repo):
    report = verify_claims([claim(["E1"])], [ev("E1")], repo)

    assert report.counts() == {"kept": 1, "flagged": 0, "dropped": 0}
    assert report.kept[0].citations == ["requirements.txt:1"]


def test_fabricated_path_is_dropped(repo):
    report = verify_claims([claim(["E1"])], [ev("E1", path="does/not/exist.py")], repo)

    assert report.counts()["dropped"] == 1
    assert report.dropped[0].reason


def test_path_traversal_is_dropped(repo):
    outside = repo.parent / "secret.py"
    outside.write_text("secret = 1\n", encoding="utf-8")

    report = verify_claims([claim(["E1"])], [ev("E1", path="../secret.py")], repo)

    assert report.counts()["dropped"] == 1


def test_wrong_quote_is_flagged(repo):
    report = verify_claims([claim(["E1"], quote="redis is a message queue")], [ev("E1")], repo)

    assert report.counts() == {"kept": 0, "flagged": 1, "dropped": 0}
    assert report.flagged[0].display_label == "unverified"
    assert "quote" in report.flagged[0].reason.lower()


def test_quote_matching_ignores_whitespace_and_case(repo):
    sources = [ev("E1", text="  Redis  ==5.0.0  \n")]
    report = verify_claims([claim(["E1"], quote="redis ==5.0.0")], sources, repo)

    assert report.counts()["kept"] == 1


# --- Line-by-line quote verification (option A) ------------------------------

def test_multiline_quote_is_verified_line_by_line(repo):
    sources = [
        ev("E1", kind="code", path="app.py", start=1, end=1, text="from functools import lru_cache"),
        ev("E2", kind="code", path="app.py", start=2, end=2, text="@lru_cache(maxsize=128)"),
    ]
    quote = "from functools import lru_cache\n@lru_cache(maxsize=128)"

    report = verify_claims([claim(["E1", "E2"], quote=quote)], sources, repo)

    assert report.counts()["kept"] == 1


def test_short_quote_fragments_are_ignored(repo):
    sources = [ev("E1")]

    report = verify_claims([claim(["E1"], quote="redis==5.0.0\n}")], sources, repo)

    assert report.counts()["kept"] == 1


def test_quote_without_a_substantive_segment_is_flagged(repo):
    report = verify_claims([claim(["E1"], quote="}\n)")], [ev("E1")], repo)

    assert report.counts()["flagged"] == 1


def test_quote_never_matches_across_a_concatenation(repo):
    sources = [
        ev("E1", start=1, end=1, text="abc"),
        ev("E2", start=2, end=2, text="def"),
    ]

    # "abcdef" only exists if the two items are glued together: must be flagged.
    report = verify_claims([claim(["E1", "E2"], quote="abcdef")], sources, repo)

    assert report.counts()["flagged"] == 1


# --- Evidence-id checks ------------------------------------------------------

def test_unknown_evidence_id_drops_claim(repo):
    report = verify_claims([claim(["E1", "E42"])], [ev("E1")], repo)

    assert report.counts()["dropped"] == 1
    assert "E42" in report.dropped[0].reason


def test_missing_quote_flags_stated_claim(repo):
    report = verify_claims([claim(["E1"], quote="")], [ev("E1")], repo)

    assert report.counts()["flagged"] == 1
    assert "quote" in report.flagged[0].reason.lower()


def test_no_evidence_drops_claim(repo):
    report = verify_claims([claim([])], [ev("E1")], repo)

    assert report.counts()["dropped"] == 1


def test_inferred_claim_requires_verified_evidence(repo):
    kept = verify_claims([claim(["E1"], label="inferred", quote=None)], [ev("E1")], repo)
    dropped = verify_claims([claim(["E1"], label="inferred", quote=None)], [ev("E1", path="missing.py")], repo)

    assert kept.counts()["kept"] == 1
    assert dropped.counts()["dropped"] == 1


def test_not_documented_claim_needs_no_evidence(repo):
    report = verify_claims([claim([], label="not_documented", quote=None)], [], repo)

    assert report.counts()["kept"] == 1


# --- Structural checks -------------------------------------------------------

def test_line_range_beyond_file_is_dropped(repo):
    report = verify_claims([claim(["E1"])], [ev("E1", start=2, end=99, text="redis==5.0.0")], repo)

    assert report.counts()["dropped"] == 1


def test_malformed_claims_do_not_crash(repo):
    prompts = [None, "string", {}, {"statement": "x", "label": "bogus", "evidence_ids": ["E1"]}]
    report = verify_claims(prompts, [ev("E1")], repo)

    assert report.counts()["dropped"] == len(prompts)


def test_evidence_collection_is_accepted(repo):
    collection = EvidenceCollection([ev("", text="redis==5.0.0")])

    report = verify_claims([claim(["E1"])], collection, repo)

    assert report.counts()["kept"] == 1


# --- Commit evidence ---------------------------------------------------------

def _make_git_repo(tmp_path: Path) -> str:
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    (tmp_path / "app.py").write_text("print('hi')\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
    subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "Add redis caching"],
        cwd=tmp_path, check=True,
    )
    return subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=tmp_path, capture_output=True, text=True, check=True,
    ).stdout.strip()


@needs_git
def test_real_commit_is_kept(tmp_path):
    head = _make_git_repo(tmp_path)
    source = ev("E1", kind="commit", path=None, start=None, end=None, text="Add redis caching", commit=head)

    report = verify_claims([claim(["E1"], quote="Add redis caching")], [source], tmp_path)

    assert report.counts()["kept"] == 1
    assert report.kept[0].citations == [f"commit {head}"]


@needs_git
def test_fake_commit_hash_is_dropped(tmp_path):
    _make_git_repo(tmp_path)
    fake = ev("E1", kind="commit", path=None, start=None, end=None, text="made up", commit="deadbeefdeadbeefdeadbeefdeadbeefdeadbeef")

    report = verify_claims([claim(["E1"], quote="made up")], [fake], tmp_path)

    assert report.counts()["dropped"] == 1


@needs_git
def test_wrong_commit_quote_is_flagged(tmp_path):
    head = _make_git_repo(tmp_path)
    source = ev("E1", kind="commit", path=None, start=None, end=None, text="Add redis caching", commit=head)

    report = verify_claims([claim(["E1"], quote="totally unrelated message")], [source], tmp_path)

    assert report.counts()["flagged"] == 1


# --- Demo hook and badge -----------------------------------------------------

def test_inject_fake_claim_is_caught(repo):
    claims = inject_fake_claim([claim(["E1"])])
    report = verify_claims(claims, [ev("E1")], repo)

    assert report.counts() == {"kept": 1, "flagged": 0, "dropped": 1}
    assert report.badge() == "1 verified, 0 flagged, 1 dropped"
