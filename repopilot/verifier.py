"""Deterministic claim verifier (no LLM, ever).

The model may only cite evidence by id. This module resolves every id back to
real repository data and decides whether a claim is kept, flagged, or dropped.
It is fully deterministic and never calls a language model.

Rules implemented (spec section 9.6):
  1. Every cited evidence id must exist, otherwise the claim is dropped.
  2. File evidence must live inside the repo (no path traversal) with a real
     line range.
  3. ``stated`` claims need a quote that appears in the cited evidence after
     whitespace/case normalization; otherwise the claim is flagged.
  4. Commit evidence needs a real hash (``git cat-file -e``) and any quoted
     text must appear in the real commit message.
  5. A claim with no verifiable evidence is dropped.
  6. ``inferred`` claims must still cite at least one verified evidence item.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

from . import config
from .evidence import Evidence, EvidenceCollection

VALID_LABELS: frozenset[str] = frozenset({"stated", "inferred", "not_documented"})
FILE_KINDS: frozenset[str] = frozenset({"code", "config", "dependency", "doc"})
COMMIT_KINDS: frozenset[str] = frozenset({"commit"})

_WS_RE = re.compile(r"\s+")
_COMMIT_RE = re.compile(r"[0-9a-fA-F]{7,40}")

# A quoted line shorter than this (after normalization) is treated as a
# fragment (e.g. "}", "})", "x") and ignored when checking a quote.
_MIN_QUOTE_SEGMENT_CHARS = 4


@dataclass
class ClaimResult:
    """Outcome for a single model claim."""

    statement: str
    declared_label: str
    status: str                 # "kept" | "flagged" | "dropped"
    reason: str | None
    evidence_ids: list[str] = field(default_factory=list)
    citations: list[str] = field(default_factory=list)
    quote: str | None = None

    @property
    def display_label(self) -> str:
        """The label shown to the user; flagged claims read as 'unverified'."""
        return "unverified" if self.status == "flagged" else self.declared_label

    def to_dict(self) -> dict:
        return {
            "statement": self.statement,
            "label": self.display_label,
            "status": self.status,
            "reason": self.reason,
            "evidence_ids": list(self.evidence_ids),
            "citations": list(self.citations),
            "quote": self.quote,
        }


@dataclass
class VerificationReport:
    """The cleaned result of verifying a batch of claims."""

    results: list[ClaimResult]

    @property
    def kept(self) -> list[ClaimResult]:
        return [r for r in self.results if r.status == "kept"]

    @property
    def flagged(self) -> list[ClaimResult]:
        return [r for r in self.results if r.status == "flagged"]

    @property
    def dropped(self) -> list[ClaimResult]:
        return [r for r in self.results if r.status == "dropped"]

    @property
    def claims(self) -> list[ClaimResult]:
        """Kept and flagged claims, in original order (dropped excluded)."""
        return [r for r in self.results if r.status != "dropped"]

    def counts(self) -> dict[str, int]:
        return {"kept": len(self.kept), "flagged": len(self.flagged), "dropped": len(self.dropped)}

    def badge(self) -> str:
        counts = self.counts()
        return f"{counts['kept']} verified, {counts['flagged']} flagged, {counts['dropped']} dropped"


def verify_claims(
    claims: Iterable[dict] | None,
    evidence: Iterable[Evidence] | EvidenceCollection | None,
    repo_root: str | Path,
) -> VerificationReport:
    """Verify model claims against the evidence and the cloned repository."""
    by_id: dict[str, Evidence] = {item.id: item for item in _as_list(evidence)}
    checker = _Checker(Path(repo_root), by_id)
    return VerificationReport([checker.check(claim) for claim in _as_list(claims)])


def inject_fake_claim(claims: Iterable[dict] | None) -> list[dict]:
    """Demo hook: append a fabricated citation so the verifier can catch it live."""
    result = [dict(claim) for claim in _as_list(claims)]
    result.append(
        {
            "statement": "Redis clusters shard the cache across 12 nodes for 5 million users.",
            "evidence_ids": ["E999"],
            "label": "stated",
            "quote": "cluster-mode enabled with 12 shards for 5M users",
        }
    )
    return result


class _Checker:
    """Stateful checker that caches file line counts for one repo."""

    def __init__(self, root: Path, by_id: dict[str, Evidence]) -> None:
        self.root = root.resolve()
        self.by_id = by_id
        self._lines_cache: dict[str, list[str] | None] = {}

    # -- public ---------------------------------------------------------------

    def check(self, claim: object) -> ClaimResult:
        if not isinstance(claim, dict):
            return self._drop("", "", "malformed claim (not a JSON object)")

        statement = str(claim.get("statement") or "").strip()
        declared = claim.get("label")
        quote = claim.get("quote")
        raw_ids = claim.get("evidence_ids")

        if not statement:
            return self._drop(statement, str(declared or ""), "missing statement")
        if declared not in VALID_LABELS:
            return self._drop(statement, str(declared or ""), "invalid label")
        if not isinstance(raw_ids, list) or not all(isinstance(i, str) for i in raw_ids):
            return self._drop(statement, declared, "evidence_ids must be a list of strings")
        if not raw_ids and declared != "not_documented":
            return self._drop(statement, declared, "no evidence cited")

        unknown = [i for i in raw_ids if i not in self.by_id]
        if unknown:
            return self._drop(
                statement, declared,
                f"fabricated or unknown evidence id(s): {', '.join(unknown)}",
            )

        cited = [self.by_id[i] for i in raw_ids]
        valid = [item for item in cited if self._is_valid(item)]
        if declared != "not_documented" and not valid:
            return self._drop(statement, declared, "no verifiable evidence")

        if declared == "stated":
            text = (quote or "").strip()
            if not text:
                return self._flagged(statement, declared, "stated claim is missing a quote", valid, quote)
            if not self._quote_found(text, valid):
                return self._flagged(
                    statement, declared, "quote not found in the cited evidence", valid, quote,
                )

        return ClaimResult(
            statement=statement,
            declared_label=declared,
            status="kept",
            reason=None,
            evidence_ids=[item.id for item in valid],
            citations=[item.location() for item in valid],
            quote=quote,
        )

    # -- evidence validation --------------------------------------------------

    def _is_valid(self, item: Evidence) -> bool:
        if item.kind in COMMIT_KINDS:
            return bool(item.commit) and self._commit_exists(item.commit)
        if item.kind in FILE_KINDS:
            lines = self._read_lines(item.path)
            if lines is None:
                return False
            start, end = item.start_line, item.end_line
            if start is None or end is None:
                return False
            return 1 <= start <= end <= len(lines)
        return False

    def _read_lines(self, rel_path: str | None) -> list[str] | None:
        if rel_path in self._lines_cache:
            return self._lines_cache[rel_path]
        resolved = self._resolve(rel_path)
        lines: list[str] | None = None
        if resolved is not None:
            try:
                text = resolved.read_text(encoding="utf-8", errors="replace")
                lines = text.splitlines()
            except (OSError, ValueError):
                lines = None
        self._lines_cache[rel_path] = lines
        return lines

    def _resolve(self, rel_path: str | None) -> Path | None:
        """Resolve a repo-relative path, rejecting traversal and escape."""
        if not isinstance(rel_path, str) or not rel_path or "\x00" in rel_path:
            return None
        candidate = Path(rel_path)
        if candidate.is_absolute():
            return None
        try:
            resolved = (self.root / candidate).resolve()
        except OSError:
            return None
        if not resolved.is_relative_to(self.root) or not resolved.is_file():
            return None
        return resolved

    # -- quote matching -------------------------------------------------------

    def _quote_found(self, quote: str, valid: list[Evidence]) -> bool:
        """Check a quote line-by-line.

        Each *substantive* newline-separated segment must appear (as a
        normalized substring) inside at least one cited evidence item. Short
        fragments are ignored, but at least one substantive segment is
        required. Segments are never matched against a concatenation of items.
        """
        segments = _quote_segments(quote)
        if not segments:
            return False
        return all(self._segment_found(segment, valid) for segment in segments)

    def _segment_found(self, segment: str, valid: list[Evidence]) -> bool:
        for item in valid:
            candidates = [item.text]
            if item.kind in COMMIT_KINDS and item.commit:
                message = self._commit_message(item.commit)
                if message:
                    candidates.append(message)
            if any(candidate and segment in _normalize(candidate) for candidate in candidates):
                return True
        return False

    # -- git helpers ----------------------------------------------------------

    def _commit_exists(self, commit: str) -> bool:
        if not _COMMIT_RE.fullmatch(commit):
            return False
        proc = _run_git(["cat-file", "-e", f"{commit}^{{commit}}"], self.root)
        return proc is not None and proc.returncode == 0

    def _commit_message(self, commit: str) -> str:
        if not _COMMIT_RE.fullmatch(commit):
            return ""
        proc = _run_git(["log", "-1", "--format=%B", commit], self.root)
        if proc is None or proc.returncode != 0:
            return ""
        return proc.stdout

    # -- result helpers -------------------------------------------------------

    def _drop(self, statement: str, declared: str, reason: str) -> ClaimResult:
        return ClaimResult(statement, declared, "dropped", reason)

    def _flagged(
        self, statement: str, declared: str, reason: str, valid: list[Evidence], quote: object,
    ) -> ClaimResult:
        return ClaimResult(
            statement=statement,
            declared_label=declared,
            status="flagged",
            reason=reason,
            evidence_ids=[item.id for item in valid],
            citations=[item.location() for item in valid],
            quote=quote if isinstance(quote, str) else None,
        )


def _run_git(args: list[str], cwd: Path) -> subprocess.CompletedProcess | None:
    """Run a read-only git command; returns None if git is unavailable."""
    try:
        return subprocess.run(
            ["git", *args],
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=config.GIT_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None


def _normalize(text: str) -> str:
    return _WS_RE.sub(" ", (text or "").strip().lower())


def _quote_segments(quote: str) -> list[str]:
    """Return substantive, normalized newline-separated quote segments."""
    segments: list[str] = []
    for raw in (quote or "").splitlines():
        normalized = _normalize(raw)
        if len(normalized) >= _MIN_QUOTE_SEGMENT_CHARS:
            segments.append(normalized)
    return segments


def _as_list(items: Iterable | None) -> list:
    if items is None:
        return []
    if isinstance(items, EvidenceCollection):
        return list(items)
    return list(items)
