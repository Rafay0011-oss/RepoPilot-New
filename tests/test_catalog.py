"""Tests for repopilot.catalog and repopilot.evidence (offline)."""

from __future__ import annotations

from pathlib import Path

from repopilot import config
from repopilot.catalog import CATALOG, classify_path, describe, detect, tags
from repopilot.evidence import Evidence, EvidenceCollection
from repopilot.loader import inventory

FIXTURES = Path(__file__).parent / "fixtures"
SAMPLE_REPO = FIXTURES / "sample_repo"


def _detect(sample_root: Path, wanted: list[str]):
    return detect(sample_root, inventory(sample_root), wanted_tags=wanted)


# --- Catalog metadata --------------------------------------------------------

def test_catalog_tags_match_config():
    assert tags() == list(config.SCALING_TAGS)


def test_every_catalog_entry_has_description_and_patterns():
    for entry in CATALOG:
        assert entry.description
        assert entry.patterns


# --- Acceptance: fixture detection ------------------------------------------

def test_redis_in_requirements_is_caching_at_line_1():
    hits = [e for e in _detect(SAMPLE_REPO, ["caching"]) if e.path == "requirements.txt"]

    assert hits, "expected caching evidence in requirements.txt"
    hit = hits[0]
    assert hit.tag == "caching"
    assert hit.kind == "dependency"
    assert hit.start_line == 1
    assert hit.end_line == 1
    assert "redis" in hit.text
    assert hit.location() == "requirements.txt:1"


def test_replicas_in_compose_is_horizontal_scaling():
    hits = [e for e in _detect(SAMPLE_REPO, ["horizontal_scaling"]) if e.path == "docker-compose.yml"]

    assert hits, "expected horizontal_scaling evidence in docker-compose.yml"
    hit = hits[0]
    assert hit.tag == "horizontal_scaling"
    assert hit.kind == "config"
    assert hit.start_line == 5
    assert hit.end_line == 5
    assert "replicas: 3" in hit.text
    assert hit.location() == "docker-compose.yml:5"


def test_python_source_hit_is_classified_as_code():
    hits = [e for e in _detect(SAMPLE_REPO, ["caching"]) if e.path == "service/cache.py"]

    assert hits, "expected caching evidence in service/cache.py"
    assert hits[0].kind == "code"


# --- Classification ----------------------------------------------------------

def test_classify_path():
    assert classify_path("requirements.txt") == "dependency"
    assert classify_path("pyproject.toml") == "dependency"
    assert classify_path("docker-compose.yml") == "config"
    assert classify_path("Dockerfile") == "config"
    assert classify_path("k8s/deployment.yaml") == "config"
    assert classify_path("service/cache.py") == "code"
    assert classify_path("README.md") == "doc"


def test_describe_known_and_unknown_tags():
    assert describe("caching")
    assert describe("does_not_exist") is None


# --- Ranking and limits ------------------------------------------------------

def test_detection_downranks_tests_paths(tmp_path):
    (tmp_path / "tests").mkdir()
    (tmp_path / "src").mkdir()
    (tmp_path / "tests" / "hit.py").write_text("x = 'redis'\n", encoding="utf-8")
    (tmp_path / "src" / "hit.py").write_text("x = 'redis'\n", encoding="utf-8")

    hits = detect(tmp_path, inventory(tmp_path), wanted_tags=["caching"])
    paths = [e.path for e in hits]

    assert paths == ["src/hit.py", "tests/hit.py"]


def test_detection_respects_max_per_tag(tmp_path):
    (tmp_path / "many.py").write_text("a = 'redis'\nb = 'redis'\nc = 'redis'\n", encoding="utf-8")

    hits = detect(tmp_path, inventory(tmp_path), wanted_tags=["caching"], max_per_tag=2)
    assert len(hits) == 2

    all_hits = detect(tmp_path, inventory(tmp_path), wanted_tags=["caching"], max_per_tag=None)
    assert len(all_hits) == 3


# --- Evidence model ----------------------------------------------------------

def test_evidence_location_formats():
    assert Evidence("E1", "code", "a.py", 12, 12, "x").location() == "a.py:12"
    assert Evidence("E1", "code", "a.py", 12, 14, "x").location() == "a.py:12-14"
    assert Evidence("E1", "commit", None, None, None, "msg", commit="abc123").location() == "commit abc123"
    assert Evidence("E1", "doc", None, None, None, "x").location() == "unknown"


def test_evidence_collection_assigns_ids_and_looks_up():
    collection = EvidenceCollection()
    first = collection.add(Evidence("", "code", "a.py", 1, 1, "x", tag="caching"))
    second = collection.add(Evidence("", "code", "b.py", 2, 2, "y", tag="caching"))

    assert first.id == "E1"
    assert second.id == "E2"
    assert len(collection) == 2
    assert collection.get("E2") is second
    assert collection.get("E9") is None
    assert collection.by_tag("caching") == [first, second]
