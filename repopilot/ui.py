"""Pure presentation helpers for the RepoPilot UI.

Deliberately free of any Streamlit import so the view-building logic can be
unit-tested offline. ``app.py`` is the thin Streamlit page that consumes
:func:`build_answer_view`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping


@dataclass(frozen=True)
class CitationView:
    """One citation shown as ``path:start-end`` with its expandable lines."""

    label: str
    block: str


@dataclass
class ClaimView:
    """A claim prepared for display."""

    label: str
    statement: str
    status: str
    quote: str | None = None
    warning: str | None = None
    citations: list[CitationView] = field(default_factory=list)


@dataclass
class AnswerView:
    """Everything the UI needs to render one answer."""

    error: str | None
    intent: str
    badge: str
    claims: list[ClaimView] = field(default_factory=list)
    dropped: list[ClaimView] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    cannot_confirm: list[str] = field(default_factory=list)


def verifier_badge(counts: Mapping[str, int] | None) -> str:
    """Return the small verifier badge text (spec 9.8)."""
    counts = counts or {}
    return (
        f"{counts.get('kept', 0)} verified, "
        f"{counts.get('flagged', 0)} flagged, "
        f"{counts.get('dropped', 0)} dropped"
    )


def citation_label(evidence: Mapping[str, Any]) -> str:
    """Build ``path:start-end`` for files or ``commit <hash>`` for commits."""
    if evidence.get("kind") == "commit":
        commit = str(evidence.get("commit") or "")[:12]
        return f"commit {commit}" if commit else "commit"
    path = evidence.get("path") or "unknown"
    start = evidence.get("start_line")
    end = evidence.get("end_line")
    if start is None:
        return str(path)
    if end is None or end == start:
        return f"{path}:{start}"
    return f"{path}:{start}-{end}"


def citation_block(evidence: Mapping[str, Any]) -> str:
    """Return the cited text, numbering lines when the range is known."""
    if evidence.get("kind") == "commit":
        return evidence.get("text") or ""
    block = evidence.get("block")
    if block:
        return str(block)
    text = str(evidence.get("text") or "")
    start = evidence.get("start_line")
    if start is None:
        return text
    lines = text.splitlines() or [text]
    return "\n".join(f"{start + offset:>4} | {line}" for offset, line in enumerate(lines))


def build_answer_view(result: Any) -> AnswerView:
    """Turn a pipeline ``Result`` (or its dict form) into an :class:`AnswerView`."""
    evidence = {item.get("id"): item for item in (_get(result, "evidence", []) or [])}
    claims = [_claim_view(claim, evidence) for claim in (_get(result, "claims", []) or [])]
    dropped = [_claim_view(claim, evidence) for claim in (_get(result, "dropped", []) or [])]
    badge = _get(result, "badge") or verifier_badge(_get(result, "counts"))
    return AnswerView(
        error=_get(result, "error"),
        intent=_get(result, "intent", "") or "",
        badge=badge,
        claims=claims,
        dropped=dropped,
        missing=list(_get(result, "missing", []) or []),
        notes=list(_get(result, "notes", []) or []),
        cannot_confirm=list(_get(result, "cannot_confirm", []) or []),
    )


def _claim_view(claim: Mapping[str, Any], evidence: Mapping[str, Any]) -> ClaimView:
    citations: list[CitationView] = []
    for evidence_id in claim.get("evidence_ids") or []:
        item = evidence.get(evidence_id)
        if item:
            citations.append(
                CitationView(
                    label=item.get("location") or citation_label(item),
                    block=citation_block(item),
                )
            )
    return ClaimView(
        label=str(claim.get("label") or "unknown"),
        statement=str(claim.get("statement") or ""),
        status=str(claim.get("status") or ""),
        quote=claim.get("quote"),
        warning=claim.get("reason") if claim.get("status") != "kept" else None,
        citations=citations,
    )


def _get(obj: Any, name: str, default: Any = None) -> Any:
    if isinstance(obj, Mapping):
        return obj.get(name, default)
    return getattr(obj, name, default)
