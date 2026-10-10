"""Question router: code decides the intent and which evidence to gather.

Small CPU models choose tools badly, so the model never picks tools. A simple
keyword router maps a question to an intent, then deterministic code gathers
evidence for that intent and caps it for the model.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Sequence

from . import catalog, config, history, indexer
from .evidence import Evidence
from .loader import FileInfo

ENTRY_POINTS: tuple[str, ...] = (
    "main.py", "app.py", "__main__.py", "manage.py", "wsgi.py", "asgi.py",
    "index.js", "server.js", "main.go", "main.rs", "main.java",
)

_TRIGGERS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("trace", ("what happens when", "walk me through", "flow", "trace", "call graph")),
    ("why", ("why", "reason", "chosen", "instead of", "rationale")),
    ("scaling", ("scale", "scal", "million", "many users", "number of users", "large number", "lots of users", "traffic", "performance", "handle load", "throughput", "concurrent")),
    ("overview", ("overview", "what does", "what is this", "explain this project", "explain the project")),
)

_TOPIC_STOPWORDS: frozenset[str] = frozenset(
    {
        "why", "reason", "reasons", "chosen", "choose", "instead", "used", "use",
        "using", "here", "this", "that", "does", "system", "project", "the", "and",
        "for", "with", "what", "when", "which", "how", "is", "are", "it", "in",
        "of", "to", "a", "an", "method", "code", "repo", "repository", "work",
        "works", "there", "please", "explain",
    }
)

_KIND_RANK: dict[str, int] = {"dependency": 0, "config": 1, "code": 2, "doc": 3}


@dataclass
class RouteResult:
    """What the router decided: intent, evidence, and honesty notes."""

    intent: str
    evidence: list[Evidence] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    topic: str | None = None


def classify(question: str) -> str:
    """Map a question to one of the router intents."""
    lowered = (question or "").lower()
    for intent, words in _TRIGGERS:
        if any(word in lowered for word in words):
            return intent
    return "general"


def gather(
    root: str | Path,
    files: Sequence[FileInfo],
    question: str,
    intent: str | None = None,
) -> RouteResult:
    """Gather capped evidence for the question's intent."""
    resolved_intent = intent or classify(question)
    if resolved_intent == "scaling":
        return _gather_scaling(root, files)
    if resolved_intent == "overview":
        return RouteResult("overview", _gather_overview(root, files))
    if resolved_intent == "why":
        return _gather_why(root, files, question)
    if resolved_intent == "trace":
        return _gather_trace(root, files, question)
    return RouteResult("general", _gather_general(root, files, question))


# --- scaling -----------------------------------------------------------------

def _gather_scaling(root: str | Path, files: Sequence[FileInfo]) -> RouteResult:
    hits = catalog.detect(root, files, max_per_tag=config.MAX_EVIDENCE_PER_TAG)
    per_tag: dict[str, list[Evidence]] = {}
    for item in hits:
        per_tag.setdefault(item.tag or "", []).append(item)

    missing = [tag for tag in config.SCALING_TAGS if tag not in per_tag]
    return RouteResult("scaling", _interleave(per_tag, config.MAX_EVIDENCE), missing=missing)


def _interleave(per_tag: dict[str, list[Evidence]], cap: int) -> list[Evidence]:
    """Round-robin across tags so the model sees diverse mechanisms."""
    queues = {tag: list(items) for tag, items in per_tag.items()}
    ordered = [tag for tag in config.SCALING_TAGS if tag in queues]
    ordered += [tag for tag in queues if tag not in ordered]

    out: list[Evidence] = []
    while len(out) < cap:
        progressed = False
        for tag in ordered:
            if queues[tag]:
                out.append(queues[tag].pop(0))
                progressed = True
                if len(out) >= cap:
                    break
        if not progressed:
            break
    return out


# --- overview ----------------------------------------------------------------

def _gather_overview(root: str | Path, files: Sequence[FileInfo]) -> list[Evidence]:
    evidence: list[Evidence] = []

    readme = next((f for f in files if Path(f.path).name.lower().startswith("readme")), None)
    if readme:
        evidence.extend(_line_evidence(root, readme.path, "doc", _nonempty(_read_lines(root, readme.path))[:3]))

    for info in files:
        if Path(info.path).name in config.DEPENDENCY_FILES:
            evidence.extend(_line_evidence(root, info.path, "dependency", _nonempty(_read_lines(root, info.path))[:2]))

    for info in files:
        if Path(info.path).name in ENTRY_POINTS:
            evidence.extend(_line_evidence(root, info.path, "code", _read_lines(root, info.path)[:8]))

    return evidence[: config.MAX_EVIDENCE]


# --- why ---------------------------------------------------------------------

def _gather_why(root: str | Path, files: Sequence[FileInfo], question: str) -> RouteResult:
    result = RouteResult("why", topic=extract_topic(question))
    topic = result.topic
    if not topic:
        result.notes.append("could not tell which topic you mean")
        return result

    candidates = _files_mentioning(root, files, topic)
    if not candidates:
        result.notes.append(f"no file mentions {topic!r}")
        return result

    evidence: list[Evidence] = []
    seen_commits: set[str] = set()
    primary = candidates[0]

    hist = history.first_commit_for_path(root, primary.path, allow_deepen=True)
    if hist.found and hist.commit:
        evidence.append(_commit_evidence(hist.commit))
        seen_commits.add(hist.commit.hash)
    elif hist.reason:
        result.notes.append(f"{primary.path}: {hist.reason}")

    for commit in history.commits_for_path(root, primary.path, limit=3):
        if commit.hash not in seen_commits:
            evidence.append(_commit_evidence(commit))
            seen_commits.add(commit.hash)

    evidence.extend(history.doc_lines_mentioning(root, files, topic, limit=2))

    for info in candidates[:1]:
        for number, line in enumerate(_read_lines(root, info.path), start=1):
            if topic in line.lower():
                evidence.append(Evidence("", "code", info.path, number, number, line, tag="why"))
                break

    result.evidence = _dedupe(evidence)[: config.MAX_EVIDENCE]
    if not result.evidence:
        result.notes.append(f"found no rationale for {topic!r}")
    return result


# --- trace -------------------------------------------------------------------

def _gather_trace(root: str | Path, files: Sequence[FileInfo], question: str) -> RouteResult:
    result = RouteResult("trace")
    index = indexer.build_index(root, files)

    start = None
    for token in _tokens(question):
        if token in _TOPIC_STOPWORDS:
            continue
        matches = index.find_symbol(token)
        if matches:
            start = matches[0]
            break

    if start is None:
        result.notes.append("no matching symbol to trace; showing keyword matches")
        result.evidence = _gather_general(root, files, question)
        return result

    chain = _follow_calls(index, start)
    evidence: list[Evidence] = []
    for position, symbol in enumerate(chain):
        evidence.extend(_symbol_def_evidence(root, symbol))
        if position + 1 < len(chain):
            site = _find_call(index, symbol, chain[position + 1])
            if site is not None:
                lines = _read_lines(root, site.path)
                if 1 <= site.lineno <= len(lines):
                    evidence.append(
                        Evidence("", "code", site.path, site.lineno, site.lineno,
                                 lines[site.lineno - 1], tag="trace")
                    )

    result.evidence = _dedupe(evidence)[: config.MAX_EVIDENCE]
    if len(chain) == 1:
        result.notes.append("found the entry symbol but no calls to trace from it")
    return result


def _symbol_def_evidence(root: str | Path, symbol: "indexer.Symbol") -> list[Evidence]:
    lines = _read_lines(root, symbol.path)
    if not (1 <= symbol.lineno <= len(lines)):
        return []
    return [
        Evidence(
            "", "code", symbol.path, symbol.lineno,
            symbol.end_lineno or symbol.lineno, lines[symbol.lineno - 1], tag="trace",
        )
    ]


def _find_call(
    index: "indexer.PythonIndex", caller: "indexer.Symbol", callee: "indexer.Symbol"
) -> "indexer.Call | None":
    for call in index.calls_from(caller.qualname) + index.calls_from(caller.name):
        if call.callee == callee.qualname or call.callee.split(".")[-1] == callee.name:
            return call
    return None


def _follow_calls(
    index: "indexer.PythonIndex", start: "indexer.Symbol", max_depth: int = 4
) -> list["indexer.Symbol"]:
    chain = [start]
    seen = {start.qualname}
    current = start
    for _ in range(max_depth):
        next_symbol = None
        callees = index.callees_of(current.qualname) + index.callees_of(current.name)
        for callee in callees:
            for symbol in index.find_symbol(callee) or index.find_symbol(callee.split(".")[-1]):
                if symbol.qualname not in seen:
                    next_symbol = symbol
                    break
            if next_symbol is not None:
                break
        if next_symbol is None:
            break
        chain.append(next_symbol)
        seen.add(next_symbol.qualname)
        current = next_symbol
    return chain


# --- general -----------------------------------------------------------------

def _gather_general(root: str | Path, files: Sequence[FileInfo], question: str) -> list[Evidence]:
    tokens = [t for t in _tokens(question) if t not in _TOPIC_STOPWORDS][:5]
    if not tokens:
        return []

    ranked: list[tuple[tuple[int, str, int], Evidence]] = []
    for info in files:
        kind = catalog.classify_path(info.path)
        for number, line in enumerate(_read_lines(root, info.path), start=1):
            lowered = line.lower()
            if any(token in lowered for token in tokens):
                ranked.append((_rank(kind, info.path, number), Evidence("", kind, info.path, number, number, line)))
    ranked.sort(key=lambda pair: pair[0])
    return [evidence for _, evidence in ranked[: config.MAX_EVIDENCE]]


# --- helpers -----------------------------------------------------------------

def extract_topic(question: str) -> str | None:
    """Pick the most specific-looking keyword from a 'why' question."""
    words = [w for w in _tokens(question) if w not in _TOPIC_STOPWORDS]
    if not words:
        return None
    return max(words, key=len)


def _tokens(text: str) -> list[str]:
    return re.findall(r"[a-zA-Z_][a-zA-Z0-9_]{2,}", (text or "").lower())


def _files_mentioning(root: str | Path, files: Sequence[FileInfo], topic: str) -> list[FileInfo]:
    matches: list[FileInfo] = []
    for info in files:
        text = "\n".join(_read_lines(root, info.path))
        if topic in info.path.lower() or topic in text.lower():
            matches.append(info)
    matches.sort(key=lambda f: (_rank(catalog.classify_path(f.path), f.path, 0), f.path))
    return matches


def _read_lines(root: str | Path, rel_path: str) -> list[str]:
    try:
        text = (Path(root) / rel_path).read_text(encoding="utf-8", errors="replace")
    except (OSError, ValueError):
        return []
    return text.splitlines()


def _nonempty(lines: Sequence[str]) -> list[str]:
    return [line for line in lines if line.strip()]


def _line_evidence(root: str | Path, rel_path: str, kind: str, lines: Sequence[str]) -> list[Evidence]:
    all_lines = _read_lines(root, rel_path)
    evidence: list[Evidence] = []
    for index, line in enumerate(all_lines, start=1):
        if line in lines:
            evidence.append(Evidence("", kind, rel_path, index, index, line))
    return evidence


def _commit_evidence(commit: history.Commit) -> Evidence:
    return Evidence("", "commit", None, None, None, commit.message, commit=commit.hash, tag="why")


def _rank(kind: str, path: str, line: int) -> tuple[int, str, int]:
    penalty = 1 if any(part in path.lower() for part in config.DOWNRANK_PATH_PARTS) else 0
    return (_KIND_RANK.get(kind, 4) + penalty, path, line)


def _dedupe(items: Sequence[Evidence]) -> list[Evidence]:
    seen: set[tuple] = set()
    out: list[Evidence] = []
    for item in items:
        key = (item.kind, item.path, item.start_line, item.commit, item.text)
        if key not in seen:
            seen.add(key)
            out.append(item)
    return out
