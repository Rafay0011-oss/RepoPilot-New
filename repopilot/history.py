"""Git-history helpers for "why" questions (original contribution).

The clone made by ``loader.py`` is shallow (``--depth``), so the oldest commit
we can see may not be the real commit that introduced a file. This module:

* detects the shallow boundary,
* never reports a boundary commit as the true introducing commit,
* performs **one** bounded ``git fetch --deepen`` (for ``why`` questions only,
  with a commit limit and a timeout),
* never crashes if the fetch fails, and
* returns ``not_documented`` with the reason ``"history truncated"`` when the
  real introducing commit is out of reach.

Every git command is an argument list; nothing is ever passed to a shell.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from . import config
from .evidence import Evidence
from .loader import FileInfo

_HASH_RE = re.compile(r"[0-9a-fA-F]{7,40}")

# File names whose lines can provide rationale ("why") evidence.
_DOC_MARKERS = ("readme", "changelog", "changes", "adr", "architecture", "decision")

TRUNCATED_REASON = "history truncated"


@dataclass(frozen=True)
class Commit:
    """A commit hash and its full message."""

    hash: str
    message: str


@dataclass(frozen=True)
class FileHistory:
    """The result of looking up a file's introducing commit."""

    path: str
    status: str                     # "found" | "not_documented"
    commit: Commit | None = None
    reason: str | None = None

    @property
    def found(self) -> bool:
        return self.status == "found"


# --- git plumbing ------------------------------------------------------------

def _run_git(
    repo_root: str | Path,
    args: Sequence[str],
    *,
    timeout: int = config.GIT_TIMEOUT_SECONDS,
) -> subprocess.CompletedProcess | None:
    """Run a read-only git command as an argument list; None if it cannot run."""
    try:
        return subprocess.run(
            ["git", *args],
            cwd=str(repo_root),
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None


def commit_message(repo_root: str | Path, commit: str) -> str:
    """Return the full commit message, or '' if the commit is not available."""
    if not _HASH_RE.fullmatch(commit or ""):
        return ""
    proc = _run_git(repo_root, ["log", "-1", "--format=%B", commit])
    if proc is None or proc.returncode != 0:
        return ""
    return proc.stdout.strip()


def commits_for_path(repo_root: str | Path, path: str, limit: int = 20) -> list[Commit]:
    """Return commits touching ``path``, newest first (at most ``limit``)."""
    if not path or "\x00" in path:
        return []
    proc = _run_git(
        repo_root,
        ["log", f"--max-count={int(limit)}", "--format=%H%x1f%B%x1e", "--", path],
    )
    if proc is None or proc.returncode != 0:
        return []

    commits: list[Commit] = []
    for record in proc.stdout.split("\x1e"):
        record = record.strip("\n")
        if not record:
            continue
        commit_hash, _, message = record.partition("\x1f")
        commit_hash = commit_hash.strip()
        if _HASH_RE.fullmatch(commit_hash):
            commits.append(Commit(commit_hash, message.strip()))
    return commits


def _oldest_commit_for_path(repo_root: str | Path, path: str) -> str | None:
    proc = _run_git(repo_root, ["log", "--format=%H", "--", path])
    if proc is None or proc.returncode != 0:
        return None
    hashes = [line.strip() for line in proc.stdout.splitlines() if line.strip()]
    return hashes[-1] if hashes else None


# --- shallow-clone awareness -------------------------------------------------

def is_shallow(repo_root: str | Path) -> bool:
    """True when the repository is a shallow clone."""
    proc = _run_git(repo_root, ["rev-parse", "--is-shallow-repository"])
    if proc is not None and proc.returncode == 0:
        return proc.stdout.strip().lower() == "true"
    return bool(_shallow_file_boundaries(repo_root))


def _git_path(repo_root: str | Path, name: str) -> Path | None:
    proc = _run_git(repo_root, ["rev-parse", "--git-path", name])
    if proc is None or proc.returncode != 0:
        return None
    value = proc.stdout.strip()
    if not value:
        return None
    candidate = Path(value)
    if not candidate.is_absolute():
        candidate = Path(repo_root) / candidate
    return candidate


def _shallow_file_boundaries(repo_root: str | Path) -> set[str]:
    shallow_file = _git_path(repo_root, "shallow")
    if shallow_file is None or not shallow_file.is_file():
        return set()
    try:
        text = shallow_file.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return set()
    return {line.strip() for line in text.splitlines() if line.strip()}


def boundary_commits(repo_root: str | Path) -> set[str]:
    """Commits that are the visible edge of a shallow clone.

    Includes the ``shallow`` file's entries and any commit that appears
    parentless (``--max-parents=0``). Only meaningful when :func:`is_shallow`
    is true; callers must gate on that.
    """
    bounds = set(_shallow_file_boundaries(repo_root))
    proc = _run_git(repo_root, ["rev-list", "--max-parents=0", "HEAD"])
    if proc is not None and proc.returncode == 0:
        bounds.update(line.strip() for line in proc.stdout.splitlines() if line.strip())
    return bounds


def commit_count(repo_root: str | Path) -> int:
    proc = _run_git(repo_root, ["rev-list", "--count", "HEAD"])
    if proc is not None and proc.returncode == 0 and proc.stdout.strip().isdigit():
        return int(proc.stdout.strip())
    return 0


def deepen_once(repo_root: str | Path, *, step: int = config.HISTORY_DEEPEN_STEP) -> bool:
    """Run one bounded ``git fetch --deepen=<step>``. Never raises.

    Returns True on success. Does nothing (returns False) when the repo is not
    shallow, when the commit limit is already reached, or when git fails.
    """
    if not is_shallow(repo_root):
        return False
    if commit_count(repo_root) >= config.HISTORY_MAX_DEPTH:
        return False
    proc = _run_git(repo_root, ["fetch", f"--deepen={int(step)}"])
    return proc is not None and proc.returncode == 0


# --- public lookup -----------------------------------------------------------

def first_commit_for_path(
    repo_root: str | Path,
    path: str,
    *,
    allow_deepen: bool = False,
) -> FileHistory:
    """Return the commit that introduced ``path``.

    A shallow boundary commit is never returned as the true introducer. When
    the boundary hides the real commit, one bounded deepen is attempted if
    ``allow_deepen`` (set only for ``why`` questions); if it still cannot be
    reached, the result is ``not_documented`` with reason ``"history truncated"``.
    """
    oldest = _oldest_commit_for_path(repo_root, path)
    if oldest is None:
        return FileHistory(path, "not_documented", None, "no commit history for this path")

    if is_shallow(repo_root) and oldest in boundary_commits(repo_root):
        if allow_deepen and deepen_once(repo_root):
            oldest = _oldest_commit_for_path(repo_root, path)
        if oldest is None or (is_shallow(repo_root) and oldest in boundary_commits(repo_root)):
            return FileHistory(path, "not_documented", None, TRUNCATED_REASON)

    return FileHistory(path, "found", Commit(oldest, commit_message(repo_root, oldest)))


def doc_lines_mentioning(
    root: str | Path,
    files: Sequence[FileInfo],
    topic: str,
    *,
    limit: int = 5,
) -> list[Evidence]:
    """Evidence from README/CHANGELOG/ADR/doc lines that mention ``topic``."""
    needle = (topic or "").strip().lower()
    if not needle:
        return []

    root_path = Path(root)
    results: list[Evidence] = []
    for info in files:
        name = Path(info.path).name.lower()
        lower_path = info.path.lower()
        is_doc = (
            name.endswith((".md", ".rst", ".adoc"))
            or any(marker in name for marker in _DOC_MARKERS)
            or lower_path.startswith("docs/")
            or "/docs/" in f"/{lower_path}/"
        )
        if not is_doc:
            continue
        try:
            content = (root_path / info.path).read_text(encoding="utf-8", errors="replace")
        except (OSError, ValueError):
            continue
        for line_number, line in enumerate(content.splitlines(), start=1):
            if needle in line.lower():
                results.append(
                    Evidence("", "doc", info.path, line_number, line_number, line, tag="why")
                )
                if len(results) >= limit:
                    return results
    return results
