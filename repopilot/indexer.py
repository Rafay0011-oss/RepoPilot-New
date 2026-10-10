"""Python-only AST index for call-graph ("trace") questions.

Parses ``.py`` files with the built-in :mod:`ast` module and records functions,
classes, imports, and best-effort call edges. Files with syntax errors are
skipped; nothing is ever imported, compiled, or executed.

Exposes :meth:`PythonIndex.find_symbol`, :meth:`callers_of`, :meth:`callees_of`,
and :meth:`import_graph`.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable


@dataclass(frozen=True)
class Symbol:
    """A function, method, or class definition."""

    name: str
    qualname: str
    kind: str            # "function" | "method" | "class"
    path: str
    lineno: int
    end_lineno: int | None


@dataclass(frozen=True)
class Call:
    """A best-effort call edge: ``caller`` calls ``callee``."""

    caller: str          # qualname of the caller ("" at module level)
    callee: str          # called name, e.g. "process" or "obj.method"
    path: str
    lineno: int


@dataclass
class PythonIndex:
    """Collected symbols, calls, and imports for a repository."""

    symbols: list[Symbol] = field(default_factory=list)
    calls: list[Call] = field(default_factory=list)
    imports: dict[str, list[str]] = field(default_factory=dict)

    def add_symbol(self, symbol: Symbol) -> None:
        self.symbols.append(symbol)

    def add_call(self, call: Call) -> None:
        self.calls.append(call)

    def add_import(self, path: str, module: str) -> None:
        self.imports.setdefault(path, []).append(module)

    def find_symbol(self, name: str) -> list[Symbol]:
        """Return symbols whose name or qualified name matches ``name``."""
        return [s for s in self.symbols if s.name == name or s.qualname == name]

    def callers_of(self, name: str) -> list[Call]:
        """Return calls whose callee matches ``name`` (exact or suffixed)."""
        return [c for c in self.calls if _matches(c.callee, name)]

    def callees_of(self, name: str) -> list[str]:
        """Return the names called from the symbol ``name``."""
        return [c.callee for c in self.calls if _matches(c.caller, name)]

    def calls_from(self, name: str) -> list[Call]:
        """Return the call edges originating from the symbol ``name``."""
        return [c for c in self.calls if _matches(c.caller, name)]

    def import_graph(self) -> dict[str, list[str]]:
        """Return a mapping of file path to imported module names."""
        return {path: list(modules) for path, modules in self.imports.items()}


def build_index(root: str | Path, files: Iterable | None = None) -> PythonIndex:
    """Parse every ``.py`` file under ``root`` into a :class:`PythonIndex`."""
    root_path = Path(root)
    if files is None:
        from .loader import inventory  # local import avoids a cycle at import time

        files = inventory(root_path)

    index = PythonIndex()
    for info in files:
        if getattr(info, "extension", "") != ".py":
            continue
        try:
            source = (root_path / info.path).read_text(encoding="utf-8", errors="replace")
        except (OSError, ValueError):
            continue
        try:
            tree = ast.parse(source, filename=info.path)
        except SyntaxError:
            continue
        _IndexVisitor(info.path, index).visit(tree)
    return index


def _matches(name: str, target: str) -> bool:
    return name == target or name.endswith("." + target)


def _callee_name(node: ast.AST) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        parts: list[str] = []
        current: ast.AST = node
        while isinstance(current, ast.Attribute):
            parts.append(current.attr)
            current = current.value
        if isinstance(current, ast.Name):
            parts.append(current.id)
        return ".".join(reversed(parts))
    return None


class _IndexVisitor(ast.NodeVisitor):
    """Walks a module, tracking the enclosing scope to build qualified names."""

    def __init__(self, path: str, index: PythonIndex) -> None:
        self.path = path
        self.index = index
        self.scope: list[tuple[str, str]] = []  # (name, kind)

    def _qualname(self, name: str) -> str:
        return ".".join([entry[0] for entry in self.scope] + [name])

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self.index.add_symbol(
            Symbol(node.name, self._qualname(node.name), "class", self.path,
                   node.lineno, getattr(node, "end_lineno", None))
        )
        self.scope.append((node.name, "class"))
        self.generic_visit(node)
        self.scope.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._visit_function(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._visit_function(node)

    def _visit_function(self, node: ast.AST) -> None:
        kind = "method" if self.scope and self.scope[-1][1] == "class" else "function"
        name = node.name
        self.index.add_symbol(
            Symbol(name, self._qualname(name), kind, self.path,
                   node.lineno, getattr(node, "end_lineno", None))
        )
        self.scope.append((name, kind))
        self.generic_visit(node)
        self.scope.pop()

    def visit_Call(self, node: ast.Call) -> None:
        callee = _callee_name(node.func)
        if callee:
            caller = ".".join(entry[0] for entry in self.scope)
            self.index.add_call(Call(caller, callee, self.path, node.lineno))
        self.generic_visit(node)

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            self.index.add_import(self.path, alias.name)
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        module = node.module or ""
        for alias in node.names:
            self.index.add_import(self.path, f"{module}.{alias.name}" if module else alias.name)
        self.generic_visit(node)
