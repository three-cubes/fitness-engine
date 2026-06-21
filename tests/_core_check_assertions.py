"""Shared assertion for the test-discipline CORE batch test modules.

``assert_no_repo_identity`` enforces the DESIGN LAW that a CORE module's
executable code carries zero repo strings (provenance docstrings may name the
donor repo, so docstring Constants are excluded via AST).
"""

from __future__ import annotations

import ast
from pathlib import Path

_REPO_TOKENS = ("kairix", "tc-agent-zone", "agent-zone", "kata")


def assert_no_repo_identity(module_file: str | None) -> None:
    """Fail if any non-docstring string literal in ``module_file`` names a repo."""
    assert module_file is not None
    text = Path(module_file).read_text(encoding="utf-8")
    tree = ast.parse(text)
    docstring_ids: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Module | ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            doc = ast.get_docstring(node, clean=False)
            if doc is not None and node.body:
                first = node.body[0]
                if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant):
                    docstring_ids.add(id(first.value))
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docstring_ids:
            lowered = node.value.lower()
            for tok in _REPO_TOKENS:
                assert tok not in lowered, f"repo identity leaked in a code literal: {tok}"
