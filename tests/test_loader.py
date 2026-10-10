"""Tests for repopilot.loader.

These tests are fully offline by default: the real clone is opt-in through the
REPOPILOT_RUN_NETWORK_TESTS environment variable.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from repopilot import config
from repopilot.loader import (
    InvalidRepoUrl,
    LoaderError,
    RepoTooLarge,
    clone_repo,
    inventory,
    normalize_url,
)

FIXTURES = Path(__file__).parent / "fixtures"
SAMPLE_REPO = FIXTURES / "sample_repo"


# --- URL validation ----------------------------------------------------------

@pytest.mark.parametrize(
    "url",
    [
        "https://github.com/octocat/Hello-World",
        "https://github.com/octocat/Hello-World.git",
        "https://github.com/octocat/Hello-World/",
    ],
)
def test_normalize_url_accepts_valid(url):
    assert normalize_url(url) == "https://github.com/octocat/Hello-World"


@pytest.mark.parametrize(
    "url",
    [
        "http://github.com/octocat/Hello-World",
        "https://gitlab.com/octocat/Hello-World",
        "git@github.com:octocat/Hello-World.git",
        "https://github.com/octocat",
        "ftp://github.com/octocat/Hello-World",
        "https://github.com/octocat/Hello-World?x=1",
        "https://github.com/octocat/Hello-World/extra",
        "",
        "   ",
        "not a url",
    ],
)
def test_normalize_url_rejects_invalid(url):
    with pytest.raises(InvalidRepoUrl):
        normalize_url(url)


# --- Cloning (mocked, offline) ----------------------------------------------

def test_clone_rejects_invalid_url_without_running_git(monkeypatch, tmp_path):
    def boom(*args, **kwargs):  # pragma: no cover - must never run
        raise AssertionError("git must not be called for an invalid URL")

    monkeypatch.setattr(subprocess, "run", boom)
    with pytest.raises(InvalidRepoUrl):
        clone_repo("https://gitlab.com/octocat/Hello-World", dest=tmp_path)


def test_clone_builds_safe_list_command(monkeypatch, tmp_path):
    captured = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        captured["kwargs"] = kwargs
        Path(cmd[-1]).mkdir(parents=True, exist_ok=True)
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    checkout = clone_repo("https://github.com/octocat/Hello-World", dest=tmp_path)

    cmd = captured["cmd"]
    assert isinstance(cmd, list)
    assert cmd[:4] == ["git", "clone", "--depth", str(config.CLONE_DEPTH)]
    assert cmd[4] == "https://github.com/octocat/Hello-World"
    assert captured["kwargs"].get("shell", False) is False
    assert checkout == tmp_path / "Hello-World"
    assert checkout.is_dir()


def test_clone_failure_returns_friendly_message(monkeypatch, tmp_path):
    stderr = "fatal: repository 'https://github.com/octocat/Nope' not found"
    monkeypatch.setattr(
        subprocess, "run",
        lambda cmd, **kw: subprocess.CompletedProcess(cmd, 128, stdout="", stderr=stderr),
    )
    with pytest.raises(LoaderError) as excinfo:
        clone_repo("https://github.com/octocat/Nope", dest=tmp_path)
    assert "not found" in str(excinfo.value).lower()
    assert "fatal" not in str(excinfo.value).lower()  # raw git output is not leaked


def test_clone_reports_missing_git(monkeypatch, tmp_path):
    def raise_missing(*args, **kwargs):
        raise FileNotFoundError("git")

    monkeypatch.setattr(subprocess, "run", raise_missing)
    with pytest.raises(LoaderError) as excinfo:
        clone_repo("https://github.com/octocat/Hello-World", dest=tmp_path)
    assert "git" in str(excinfo.value).lower()


# --- Inventory ---------------------------------------------------------------

def test_inventory_includes_text_files_and_ignores_junk():
    paths = {f.path for f in inventory(SAMPLE_REPO)}

    assert "README.md" in paths
    assert "requirements.txt" in paths
    assert "docker-compose.yml" in paths
    assert "app.py" in paths
    assert "service/cache.py" in paths

    assert not any(p.startswith("node_modules/") for p in paths)
    assert "logo.png" not in paths  # binary extension


def test_inventory_marks_source_files():
    files = {f.path: f for f in inventory(SAMPLE_REPO)}

    assert files["app.py"].is_source is True
    assert files["app.py"].extension == ".py"
    assert files["requirements.txt"].is_source is False
    assert files["requirements.txt"].extension == ".txt"


def test_inventory_skips_oversized_files(tmp_path):
    (tmp_path / "small.py").write_text("x = 1\n", encoding="utf-8")
    big = tmp_path / "big.py"
    big.write_text("x = 1\n" * 60_000, encoding="utf-8")
    assert big.stat().st_size > config.MAX_FILE_BYTES

    paths = {f.path for f in inventory(tmp_path)}
    assert "small.py" in paths
    assert "big.py" not in paths


def test_inventory_accepts_exactly_the_source_limit(tmp_path):
    for i in range(config.MAX_SOURCE_FILES):
        (tmp_path / f"f{i}.py").write_text("x = 1\n", encoding="utf-8")

    assert len(inventory(tmp_path)) == config.MAX_SOURCE_FILES


def test_inventory_raises_repo_too_large(tmp_path):
    for i in range(config.MAX_SOURCE_FILES + 1):
        (tmp_path / f"m{i}.py").write_text("x = 1\n", encoding="utf-8")

    with pytest.raises(RepoTooLarge):
        inventory(tmp_path)


# --- Opt-in real network clone ----------------------------------------------

@pytest.mark.skipif(
    not os.environ.get("REPOPILOT_RUN_NETWORK_TESTS"),
    reason="set REPOPILOT_RUN_NETWORK_TESTS=1 to enable the real clone test",
)
def test_clone_real_small_public_repo():
    checkout = clone_repo("https://github.com/octocat/Hello-World")
    files = inventory(checkout)
    assert files, "expected at least one file in the cloned repo"
