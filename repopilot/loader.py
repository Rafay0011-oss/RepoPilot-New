"""Clone and inventory a public GitHub repository.

Repository content is untrusted data. This module only validates a URL,
shallow-clones it with ``git``, and lists/stats files. It never imports,
executes, or evaluates anything from the target repository.
"""

from __future__ import annotations

import os
import re
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from . import config


class LoaderError(Exception):
    """Base class for loader problems, always carrying a plain-language message."""


class InvalidRepoUrl(LoaderError):
    """Raised when a URL is not an accepted public GitHub repository URL."""


class RepoTooLarge(LoaderError):
    """Raised when a repository has more source files than the prototype allows."""


@dataclass(frozen=True)
class FileInfo:
    """A single inventoried file inside a cloned repository."""

    path: str        # repo-relative POSIX path, e.g. "service/cache.py"
    size: int        # size in bytes
    is_source: bool  # True when the extension is a known source extension
    extension: str   # lower-case extension including the dot, e.g. ".py"


_URL_RE = re.compile(config.GITHUB_URL_PATTERN)


def normalize_url(url: str) -> str:
    """Validate a user-supplied URL and return its canonical form.

    Trailing slashes and a trailing ``.git`` are removed so that
    ``.../repo``, ``.../repo/`` and ``.../repo.git`` normalize identically.

    Raises:
        InvalidRepoUrl: if the URL is not ``https://github.com/<owner>/<repo>``.
    """
    if not isinstance(url, str) or not url.strip():
        raise InvalidRepoUrl(
            "Please enter a GitHub repository URL, "
            "for example https://github.com/owner/repo."
        )
    candidate = url.strip()
    if not _URL_RE.match(candidate):
        raise InvalidRepoUrl(
            "Only public GitHub URLs of the form "
            "https://github.com/owner/repo are supported."
        )
    candidate = candidate.rstrip("/")
    if candidate.endswith(".git"):
        candidate = candidate[:-4]
    return candidate


def clone_repo(url: str, dest: str | Path | None = None) -> Path:
    """Shallow-clone a validated public GitHub repo and return the checkout path.

    Args:
        url: A public GitHub URL.
        dest: Optional parent directory. A fresh temp directory is used when
            omitted.

    Raises:
        InvalidRepoUrl: if ``url`` is not an accepted GitHub URL.
        LoaderError: if git is missing, times out, or fails.
    """
    clone_url = normalize_url(url)
    repo_name = _repo_name(clone_url)

    parent = Path(dest) if dest is not None else Path(tempfile.mkdtemp(prefix="repopilot-clone-"))
    parent.mkdir(parents=True, exist_ok=True)
    checkout = parent / repo_name
    if checkout.exists():
        raise LoaderError(f"A folder named {repo_name!r} already exists in the destination.")

    # Always a list, never a shell string: user input is never interpreted.
    cmd = ["git", "clone", "--depth", str(config.CLONE_DEPTH), clone_url, str(checkout)]
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=config.GIT_TIMEOUT_SECONDS,
            check=False,
        )
    except FileNotFoundError:
        raise LoaderError("Git is not installed or not on PATH.") from None
    except subprocess.TimeoutExpired:
        raise LoaderError(
            "Cloning timed out. Check your internet connection and try again."
        ) from None

    if proc.returncode != 0:
        raise LoaderError(_friendly_clone_error(proc.stderr))
    return checkout


def inventory(root: str | Path) -> list[FileInfo]:
    """List usable files in a cloned repository.

    Skips ignored directories, binary files, and files over the size limit.
    Raises :class:`RepoTooLarge` when the source-file count is over the limit.
    """
    root_path = Path(root).resolve()
    if not root_path.is_dir():
        raise LoaderError(f"Not a directory: {root}")

    files: list[FileInfo] = []
    source_count = 0

    for dirpath, dirnames, filenames in os.walk(root_path):
        dirnames[:] = sorted(
            name
            for name in dirnames
            if name not in config.IGNORE_DIRS and not _escapes(root_path, Path(dirpath) / name)
        )
        for name in sorted(filenames):
            full = Path(dirpath) / name
            if _escapes(root_path, full):
                continue

            extension = full.suffix.lower()
            if extension in config.BINARY_EXTENSIONS:
                continue
            try:
                size = full.stat().st_size
            except OSError:
                continue
            if size > config.MAX_FILE_BYTES:
                continue

            is_source = extension in config.SOURCE_EXTENSIONS
            if is_source:
                source_count += 1
                if source_count > config.MAX_SOURCE_FILES:
                    raise RepoTooLarge(
                        f"This repository has more than {config.MAX_SOURCE_FILES} "
                        "source files, so it is too large for this prototype."
                    )

            rel = full.relative_to(root_path).as_posix()
            files.append(FileInfo(path=rel, size=size, is_source=is_source, extension=extension))

    return files


def _repo_name(url: str) -> str:
    """Return the repository name from a normalized GitHub URL."""
    tail = url.rstrip("/")
    if tail.endswith(".git"):
        tail = tail[:-4]
    return tail.rsplit("/", 1)[-1]


def _escapes(root: Path, candidate: Path) -> bool:
    """True when ``candidate`` resolves outside ``root`` (e.g. a symlink)."""
    try:
        resolved = candidate.resolve()
    except OSError:
        return True
    return not resolved.is_relative_to(root)


def _friendly_clone_error(stderr: str) -> str:
    """Map raw git stderr to a short plain-language message."""
    text = (stderr or "").lower()
    if "could not resolve host" in text or "unable to access" in text or "timed out" in text:
        return "Could not reach GitHub. Check your internet connection and try again."
    if "not found" in text or "repository not found" in text:
        return "Repository not found. Check the URL, or make sure the repository is public."
    if "authentication" in text or "terminal prompts disabled" in text or "permission denied" in text:
        return "This repository needs authentication. RepoPilot supports public repositories only."
    if "already exists" in text:
        return "The destination folder already exists."
    return "Could not clone the repository. Please check the URL and try again."
