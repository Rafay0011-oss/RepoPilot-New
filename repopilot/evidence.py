"""The evidence data model.

Evidence is the only thing the language model is allowed to cite. Every piece
of evidence points at a real file/line or a real commit inside the cloned repo,
so the deterministic verifier can resolve it later without any model help.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Iterable, Iterator


@dataclass
class Evidence:
    """A single verifiable fact extracted from a repository."""

    id: str                       # "E1", "E2", ... (assigned by EvidenceCollection)
    kind: str                     # "code" | "config" | "dependency" | "commit" | "doc"
    path: str | None              # repo-relative file path
    start_line: int | None
    end_line: int | None
    text: str                     # exact text (code lines or commit message)
    commit: str | None = None     # commit hash when kind == "commit"
    tag: str | None = None        # e.g. "caching", "queue_background"

    def location(self) -> str:
        """Return a short human-readable citation like ``path:12-14``."""
        if self.kind == "commit":
            return f"commit {self.commit}" if self.commit else "commit"
        if not self.path:
            return "unknown"
        if self.start_line is None:
            return self.path
        if self.end_line is None or self.end_line == self.start_line:
            return f"{self.path}:{self.start_line}"
        return f"{self.path}:{self.start_line}-{self.end_line}"

    def to_dict(self) -> dict:
        """Serialise the evidence (used for the self-contained UI payload)."""
        return {
            "id": self.id,
            "kind": self.kind,
            "path": self.path,
            "start_line": self.start_line,
            "end_line": self.end_line,
            "text": self.text,
            "commit": self.commit,
            "tag": self.tag,
            "location": self.location(),
        }


class EvidenceCollection:
    """An ordered collection that assigns stable ``E1``, ``E2``, ... ids.

    The router gathers raw :class:`Evidence` items (usually with an empty id);
    adding them here fixes the ids that are shown to the model and later
    resolved by the verifier.
    """

    def __init__(self, items: Iterable[Evidence] | None = None) -> None:
        self._items: list[Evidence] = []
        if items is not None:
            for item in items:
                self.add(item)

    def add(self, evidence: Evidence) -> Evidence:
        """Append evidence, assigning it the next id, and return the copy."""
        numbered = replace(evidence, id=f"E{len(self._items) + 1}")
        self._items.append(numbered)
        return numbered

    def extend(self, items: Iterable[Evidence]) -> list[Evidence]:
        return [self.add(item) for item in items]

    def get(self, evidence_id: str) -> Evidence | None:
        for item in self._items:
            if item.id == evidence_id:
                return item
        return None

    def by_tag(self, tag: str) -> list[Evidence]:
        return [item for item in self._items if item.tag == tag]

    def items(self) -> list[Evidence]:
        return list(self._items)

    def __len__(self) -> int:
        return len(self._items)

    def __iter__(self) -> Iterator[Evidence]:
        return iter(self._items)

    def __getitem__(self, index: int) -> Evidence:
        return self._items[index]
