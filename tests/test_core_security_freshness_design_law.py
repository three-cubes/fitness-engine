"""Design-law guard for the v0.6.0 security-freshness CORE batch.

DESIGN LAW: a CORE module's EXECUTABLE code carries zero repo identity (no
taz/kairix/kata paths, globs, or thresholds). Provenance docstrings MAY name
the donor repo, so this strips docstrings via AST and scans only the executable
string literals.
"""

from __future__ import annotations

import ast
import importlib
from pathlib import Path

import pytest

_MODULES = (
    "tc_fitness.core_checks.no_real_names",
    "tc_fitness.core_checks.no_logging_secrets",
    "tc_fitness.core_checks.no_internal_patches",
    "tc_fitness.core_checks.no_internal_patches_ts",
    "tc_fitness.core_checks.license_present",
    "tc_fitness.core_checks.adr_number_unique",
)

_REPO_TOKENS = ("kairix", "tc-agent-zone", "agent-zone", "kata")


def _docstring_node_ids(tree: ast.AST) -> set[int]:
    out: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Module | ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            doc = ast.get_docstring(node, clean=False)
            if doc is not None and node.body:
                first = node.body[0]
                if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant):
                    out.add(id(first.value))
    return out


@pytest.mark.parametrize("module_name", _MODULES)
def test_no_repo_strings_in_executable_code(module_name: str) -> None:
    mod = importlib.import_module(module_name)
    text = Path(mod.__file__).read_text(encoding="utf-8")  # type: ignore[arg-type]
    tree = ast.parse(text)
    docstring_ids = _docstring_node_ids(tree)
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if id(node) in docstring_ids:
                continue
            lowered = node.value.lower()
            for tok in _REPO_TOKENS:
                assert tok not in lowered, f"{module_name}: repo identity leaked in a code literal: {tok}"


@pytest.mark.parametrize("module_name", _MODULES)
def test_module_exposes_build_and_main(module_name: str) -> None:
    mod = importlib.import_module(module_name)
    assert callable(mod.build)
    assert callable(mod.main)
