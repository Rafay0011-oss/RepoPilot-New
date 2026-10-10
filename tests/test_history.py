"""Tests for repopilot.history (offline; local git only)."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from repopilot import history
from repopilot.loader import inventory

needs_git = pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")

pytestmark = needs_git


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args], cwd=str(repo), check=True, capture_output=True, text=True,
    )


def _make_origin(tmp_path: Path) -> Path:
    origin = tmp_path / "origin"
    origin.mkdir()
    _git(origin, "init", "-q")
    _git(origin, "config", "user.email", "t@t")
    _git(origin, "config", "user.name", "t")

    (origin / "README.md").write_text("# Demo\nAuth is handled here.\n", encoding="utf-8")
    _git(origin, "add", ".")
    _git(origin, "commit", "-q", "-m", "Initial commit")

    (origin / "auth.py").write_text("def login():\n    return False\n", encoding="utf-8")
    _git(origin, "add", ".")
    _git(origin, "commit", "-q", "-m", "Add auth module")

    (origin / "auth.py").write_text("def login():\n    return True\n", encoding="utf-8")
    _git(origin, "add", ".")
    _git(origin, "commit", "-q", "-m", "Improve login")
    return origin


def _shallow_clone(origin: Path, dest: Path) -> Path:
    subprocess.run(
        ["git", "clone", "-q", "--depth", "1", "--no-local", str(origin), str(dest)],
        check=True, capture_output=True, text=True,
    )
    return dest


@pytest.fixture
def full_repo(tmp_path: Path) -> Path:
    return _make_origin(tmp_path)


@pytest.fixture
def shallow_repo(tmp_path: Path) -> Path:
    origin = _make_origin(tmp_path)
    return _shallow_clone(origin, tmp_path / "clone")


# --- Full-history lookups ----------------------------------------------------

def test_first_commit_returns_introducing_commit_and_message(full_repo):
    result = history.first_commit_for_path(full_repo, "auth.py")

    assert result.found
    assert result.commit.message == "Add auth module"


def test_commits_for_path_is_newest_first(full_repo):
    messages = [c.message for c in history.commits_for_path(full_repo, "auth.py")]

    assert messages == ["Improve login", "Add auth module"]


def test_commits_for_path_respects_limit(full_repo):
    assert len(history.commits_for_path(full_repo, "auth.py", limit=1)) == 1


def test_commit_message(full_repo):
    head = history.commits_for_path(full_repo, "auth.py", limit=1)[0].hash

    assert history.commit_message(full_repo, head) == "Improve login"


def test_missing_path_has_no_history(full_repo):
    result = history.first_commit_for_path(full_repo, "nope.py")

    assert not result.found
    assert result.commit is None


# --- Shallow-clone boundaries ------------------------------------------------

def test_shallow_clone_is_detected(shallow_repo):
    assert history.is_shallow(shallow_repo)


def test_boundary_is_never_reported_as_introducer(shallow_repo):
    result = history.first_commit_for_path(shallow_repo, "auth.py", allow_deepen=False)

    assert not result.found
    assert result.commit is None
    assert result.reason == "history truncated"


def test_one_deepen_finds_the_true_introducing_commit(shallow_repo):
    result = history.first_commit_for_path(shallow_repo, "auth.py", allow_deepen=True)

    assert result.found
    assert result.commit.message == "Add auth module"


def test_deepen_failure_does_not_crash(shallow_repo):
    _git(shallow_repo, "remote", "remove", "origin")  # make the fetch fail

    result = history.first_commit_for_path(shallow_repo, "auth.py", allow_deepen=True)

    assert not result.found
    assert result.reason == "history truncated"


def test_deepen_is_a_noop_on_a_full_repo(full_repo):
    assert history.deepen_once(full_repo) is False


# --- Doc rationale lines -----------------------------------------------------

def test_doc_lines_mentioning_topic(full_repo):
    hits = history.doc_lines_mentioning(full_repo, inventory(full_repo), "auth")

    assert hits
    assert hits[0].kind == "doc"
    assert hits[0].path == "README.md"
    assert "auth" in hits[0].text.lower()
