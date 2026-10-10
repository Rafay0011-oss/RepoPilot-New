"""Tests for repopilot.indexer and the router's trace intent (offline)."""

from __future__ import annotations

from pathlib import Path

import pytest

from repopilot import indexer, router
from repopilot.loader import inventory

FLOW = '''"""Request pipeline."""
import os


def handle_request():
    return process()


def process():
    return finalize()


def finalize():
    return True
'''

UTIL = '''class Helper:
    def run(self):
        return helper_step()


def helper_step():
    return 1
'''

BAD = '''def broken(:
    pass
'''


@pytest.fixture
def py_repo(tmp_path: Path) -> Path:
    (tmp_path / "flow.py").write_text(FLOW, encoding="utf-8")
    (tmp_path / "util.py").write_text(UTIL, encoding="utf-8")
    (tmp_path / "bad.py").write_text(BAD, encoding="utf-8")
    return tmp_path


# --- Indexer -----------------------------------------------------------------

def test_find_symbol(py_repo):
    index = indexer.build_index(py_repo)

    found = index.find_symbol("handle_request")
    assert len(found) == 1
    symbol = found[0]
    assert symbol.kind == "function"
    assert symbol.path == "flow.py"
    assert symbol.lineno > 0
    assert symbol.end_lineno >= symbol.lineno


def test_find_class_and_method(py_repo):
    index = indexer.build_index(py_repo)

    assert index.find_symbol("Helper")[0].kind == "class"
    method = index.find_symbol("Helper.run")
    assert method and method[0].kind == "method"
    assert method[0].name == "run"


def test_callers_of(py_repo):
    index = indexer.build_index(py_repo)

    callers = index.callers_of("finalize")
    assert [call.caller for call in callers] == ["process"]
    assert callers[0].path == "flow.py"

    assert index.callers_of("helper_step")[0].caller == "Helper.run"


def test_callees_of(py_repo):
    index = indexer.build_index(py_repo)

    assert "process" in index.callees_of("handle_request")
    assert "finalize" in index.callees_of("process")


def test_calls_from(py_repo):
    index = indexer.build_index(py_repo)

    edges = index.calls_from("handle_request")
    assert [edge.callee for edge in edges] == ["process"]
    assert edges[0].lineno > 0


def test_import_graph(py_repo):
    index = indexer.build_index(py_repo)

    graph = index.import_graph()
    assert "os" in graph["flow.py"]


def test_bad_syntax_is_skipped(py_repo):
    # Must not raise, and the broken file contributes nothing.
    index = indexer.build_index(py_repo)

    assert all(symbol.path != "bad.py" for symbol in index.symbols)
    assert all(call.path != "bad.py" for call in index.calls)


def test_build_index_with_inventoried_files(py_repo):
    index = indexer.build_index(py_repo, inventory(py_repo))

    assert index.find_symbol("handle_request")


# --- Router trace ------------------------------------------------------------

def test_trace_follows_call_chain(py_repo):
    route = router.gather(py_repo, inventory(py_repo), "Walk me through handle_request")

    assert route.intent == "trace"
    paths = [(item.path, item.start_line) for item in route.evidence]
    assert ("flow.py", 5) in paths   # def handle_request
    assert ("flow.py", 6) in paths   # call site: return process()
    assert ("flow.py", 9) in paths   # def process
    assert ("flow.py", 10) in paths  # call site: return finalize()
    assert ("flow.py", 13) in paths  # def finalize
    assert len(route.evidence) <= 6
    assert all(item.tag == "trace" for item in route.evidence)


def test_trace_without_symbol_falls_back(py_repo):
    route = router.gather(py_repo, inventory(py_repo), "Walk me through zzzzz")

    assert route.intent == "trace"
    assert any("no matching symbol" in note for note in route.notes)


def test_trace_without_calls_notes_it(py_repo):
    route = router.gather(py_repo, inventory(py_repo), "Walk me through finalize")

    assert route.intent == "trace"
    assert any("no calls to trace" in note for note in route.notes)
