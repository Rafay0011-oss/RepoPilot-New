"""Pipeline: load, route, gather, ask the local model, and verify.

Ties every module together and always returns a :class:`Result` — never a
crash. Results can be cached per (repository URL, question) to make demos fast.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

from . import config, llm, loader, router, verifier
from .evidence import EvidenceCollection

DEFAULT_CANNOT_CONFIRM = (
    "Deployment settings, hardware, and real traffic are not in the repository, "
    "so actual capacity cannot be confirmed from the code."
)


@dataclass
class Result:
    """The final, verified answer shown to the user."""

    repo_url: str
    question: str
    intent: str
    evidence: list[dict] = field(default_factory=list)
    claims: list[dict] = field(default_factory=list)
    dropped: list[dict] = field(default_factory=list)
    cannot_confirm: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    counts: dict[str, int] = field(default_factory=dict)
    badge: str = ""
    error: str | None = None
    cached: bool = False

    @property
    def ok(self) -> bool:
        return self.error is None

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "Result":
        fields = {f for f in cls.__dataclass_fields__}
        return cls(**{key: value for key, value in data.items() if key in fields})


def answer_path(
    root: str | Path,
    question: str,
    *,
    inject_fake: bool = False,
    client: object | None = None,
    repo_url: str = "",
) -> Result:
    """Answer a question about an already-cloned repository path."""
    root_path = Path(root)
    try:
        files = loader.inventory(root_path)
    except loader.LoaderError as exc:
        return _error(repo_url, question, str(exc))

    route = router.gather(root_path, files, question)
    collection = EvidenceCollection(route.evidence)

    try:
        model_output = llm.explain(question, collection, route.intent, client=client)
    except llm.LLMError as exc:
        return _error(repo_url, question, str(exc), intent=route.intent)

    raw_claims = model_output.get("claims") if isinstance(model_output, dict) else []
    if inject_fake:
        raw_claims = verifier.inject_fake_claim(raw_claims)

    report = verifier.verify_claims(raw_claims, collection, root_path)

    cannot_confirm = [
        str(item) for item in (model_output.get("cannot_confirm") or []) if str(item).strip()
    ]
    if route.intent == "scaling" and not cannot_confirm:
        cannot_confirm.append(DEFAULT_CANNOT_CONFIRM)

    return Result(
        repo_url=repo_url,
        question=question,
        intent=route.intent,
        evidence=[_evidence_snapshot(root_path, item) for item in collection],
        claims=[claim.to_dict() for claim in report.claims],
        dropped=[claim.to_dict() for claim in report.dropped],
        cannot_confirm=cannot_confirm,
        missing=list(route.missing),
        notes=list(route.notes),
        counts=report.counts(),
        badge=report.badge(),
    )


def answer(
    repo_url: str,
    question: str,
    *,
    inject_fake: bool = False,
    use_cache: bool = True,
    client: object | None = None,
) -> Result:
    """Clone (or reuse cache) and answer a question about a public GitHub repo."""
    try:
        url = loader.normalize_url(repo_url)
    except loader.InvalidRepoUrl as exc:
        return _error(repo_url, question, str(exc))

    if use_cache:
        cached = _load_cache(url, question, inject_fake)
        if cached is not None:
            cached.cached = True
            return cached

    try:
        checkout = loader.clone_repo(url)
    except loader.LoaderError as exc:
        return _error(url, question, str(exc))

    result = answer_path(
        checkout, question, inject_fake=inject_fake, client=client, repo_url=url
    )

    if use_cache and result.ok:
        _save_cache(url, question, inject_fake, result)
    return result


# --- error helper ------------------------------------------------------------

def _error(repo_url: str, question: str, message: str, intent: str = "") -> Result:
    return Result(repo_url=repo_url, question=question, intent=intent, error=message)


# --- evidence snapshots (self-contained UI payload) --------------------------

def _evidence_snapshot(root: Path, item) -> dict:
    """Serialise one evidence item, including the exact cited lines."""
    snapshot = item.to_dict()
    snapshot["block"] = _render_block(root, item)
    return snapshot


def _render_block(root: Path, item) -> str:
    """Return the cited text, numbered by line for file evidence."""
    if item.kind == "commit":
        return item.text or ""
    if not item.path or item.start_line is None:
        return item.text or ""
    try:
        lines = (root / item.path).read_text(encoding="utf-8", errors="replace").splitlines()
    except (OSError, ValueError):
        return item.text or ""
    start = item.start_line
    end = item.end_line or start
    if not (1 <= start <= len(lines)):
        return item.text or ""
    end = min(end, len(lines))
    return "\n".join(f"{number:>4} | {lines[number - 1]}" for number in range(start, end + 1))


# --- cache -------------------------------------------------------------------

def _cache_file(url: str, question: str, inject_fake: bool) -> Path:
    raw = f"{url}\x00{question}\x00{int(inject_fake)}".encode("utf-8")
    digest = hashlib.sha256(raw).hexdigest()[:16]
    return Path(config.CACHE_DIR) / f"{digest}.json"


def _load_cache(url: str, question: str, inject_fake: bool) -> Result | None:
    path = _cache_file(url, question, inject_fake)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return Result.from_dict(data)
    except (OSError, ValueError):
        return None


def _save_cache(url: str, question: str, inject_fake: bool, result: Result) -> None:
    path = _cache_file(url, question, inject_fake)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(result.to_dict(), indent=2), encoding="utf-8")
    except OSError:
        pass
