"""Scaling-pattern catalog and detectors (original contribution).

The catalog is a table of scaling mechanisms. Each entry has a ``tag``, a plain
description, and text detectors that are matched against dependency files,
config files, and source text. Detections become :class:`Evidence` items that
carry the exact ``file:line`` where the pattern was found.

Repository content is untrusted data: it is only read as text and matched with
regular expressions. Nothing from a target repo is ever executed.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence

from . import config
from .evidence import Evidence
from .loader import FileInfo


@dataclass(frozen=True)
class CatalogEntry:
    """One scaling mechanism: a tag, a description, and its line detectors."""

    tag: str
    description: str
    patterns: tuple[re.Pattern[str], ...]


def _patterns(*raw: str) -> tuple[re.Pattern[str], ...]:
    return tuple(re.compile(item, re.IGNORECASE) for item in raw)


# The catalog. Order here is the order evidence is returned in.
CATALOG: tuple[CatalogEntry, ...] = (
    CatalogEntry(
        tag="caching",
        description="Caching layer or in-process memoization.",
        patterns=_patterns(
            r"redis", r"memcached", r"lru_cache", r"cache_page", r"@cache\b",
            r"cachetools", r"functools\.cache", r"django\.core\.cache",
        ),
    ),
    CatalogEntry(
        tag="queue_background",
        description="Background jobs or a message queue.",
        patterns=_patterns(
            r"celery", r"\brq\b", r"kafka", r"rabbitmq", r"sidekiq", r"\bbull\b",
            r"dramatiq", r"\bhuey\b", r"\bkombu\b", r"\bamqp\b",
        ),
    ),
    CatalogEntry(
        tag="db_scaling",
        description="Database scaling: pooling, replicas, sharding, partitioning.",
        patterns=_patterns(
            r"pool_size", r"max_overflow", r"connection_?pool", r"pgbouncer",
            r"read_?replica", r"read replica", r"\bshard", r"\bpartition",
            r"create index",
        ),
    ),
    CatalogEntry(
        tag="horizontal_scaling",
        description="Running more instances behind a load balancer or autoscaler.",
        patterns=_patterns(
            r"replicas:", r"replicacount", r"maxreplicas", r"minreplicas",
            r"autoscal", r"load balanc", r"loadbalanc", r"\bupstream\b",
            r"\bhpa\b", r"horizontalpodautoscaler",
        ),
    ),
    CatalogEntry(
        tag="stateless_auth",
        description="Token-based / stateless authentication.",
        patterns=_patterns(
            r"jwt", r"pyjwt", r"\bjose\b", r"oauth", r"bearer", r"passport",
            r"jsonwebtoken", r"auth0",
        ),
    ),
    CatalogEntry(
        tag="rate_limiting",
        description="Request rate limiting or throttling.",
        patterns=_patterns(
            r"ratelimit", r"rate_limit", r"rate-limit", r"limiter", r"throttle",
            r"slowapi", r"bottleneck",
        ),
    ),
    CatalogEntry(
        tag="async_io",
        description="Async I/O or multi-worker serving.",
        patterns=_patterns(
            r"asyncio", r"async\s+def", r"aiohttp", r"uvicorn", r"gunicorn",
            r"gevent", r"eventlet", r"httpx", r"starlette", r"fastapi",
            r"worker_class", r"worker_connections",
        ),
    ),
)

_ENTRY_BY_TAG: dict[str, CatalogEntry] = {entry.tag: entry for entry in CATALOG}

# Lower rank = preferred when trimming evidence for the same tag.
_KIND_RANK: dict[str, int] = {"dependency": 0, "config": 1, "code": 2, "doc": 3}


def tags() -> list[str]:
    """Return every catalog tag, in catalog order."""
    return [entry.tag for entry in CATALOG]


def describe(tag: str) -> str | None:
    """Return the plain-language description for a tag, if it exists."""
    entry = _ENTRY_BY_TAG.get(tag)
    return entry.description if entry else None


def classify_path(path: str) -> str:
    """Classify a repo-relative path as dependency/config/code/doc."""
    name = Path(path).name
    suffix = Path(path).suffix.lower()

    if name in config.DEPENDENCY_FILES:
        return "dependency"
    if name in config.CONFIG_FILE_NAMES or suffix in {".yml", ".yaml", ".toml", ".ini", ".cfg", ".conf"}:
        return "config"
    if suffix in config.SOURCE_EXTENSIONS:
        return "code"
    return "doc"


def detect(
    root: str | Path,
    files: Sequence[FileInfo],
    *,
    wanted_tags: Iterable[str] | None = None,
    max_per_tag: int | None = config.MAX_EVIDENCE_PER_TAG,
) -> list[Evidence]:
    """Scan inventoried files and return catalog evidence with exact locations.

    Args:
        root: Repository root that ``files`` paths are relative to.
        files: Inventory from :func:`repopilot.loader.inventory`.
        wanted_tags: Restrict detection to these tags (default: all).
        max_per_tag: Keep at most this many hits per tag, best-ranked first.
            Pass ``None`` to keep every hit.
    """
    root_path = Path(root)
    wanted = [
        entry for entry in CATALOG
        if wanted_tags is None or entry.tag in set(wanted_tags)
    ]

    per_tag: dict[str, list[Evidence]] = {entry.tag: [] for entry in wanted}

    for info in files:
        kind = classify_path(info.path)
        try:
            content = (root_path / info.path).read_text(encoding="utf-8", errors="replace")
        except (OSError, ValueError):
            continue

        for line_number, line in enumerate(content.splitlines(), start=1):
            for entry in wanted:
                if _matches(entry.patterns, line):
                    per_tag[entry.tag].append(
                        Evidence(
                            id="",
                            kind=kind,
                            path=info.path,
                            start_line=line_number,
                            end_line=line_number,
                            text=line,
                            tag=entry.tag,
                        )
                    )

    evidence: list[Evidence] = []
    for entry in wanted:
        hits = sorted(per_tag[entry.tag], key=_rank)
        evidence.extend(hits if max_per_tag is None else hits[:max_per_tag])
    return evidence


def _matches(patterns: tuple[re.Pattern[str], ...], line: str) -> bool:
    return any(pattern.search(line) for pattern in patterns)


def _rank(evidence: Evidence) -> tuple[int, str, int]:
    penalty = 1 if _is_downranked(evidence.path or "") else 0
    return (_KIND_RANK.get(evidence.kind, 4) + penalty, evidence.path or "", evidence.start_line or 0)


def _is_downranked(path: str) -> bool:
    lowered = path.lower()
    return any(part in lowered for part in config.DOWNRANK_PATH_PARTS)
