"""Tests for the exemplar CORE check no_duplicate_string (v0.6.0)."""

from __future__ import annotations

from pathlib import Path

from tc_fitness.core_checks.no_duplicate_string import (
    NoDuplicateString,
    build,
    main,
    module_has_duplicate,
)

_DUP = """
def a() -> None:
    raise ValueError("search query must be empty")

def b() -> None:
    raise ValueError("search query must be empty")

def c() -> None:
    raise ValueError("search query must be empty")
"""

_CLEAN = """
_MSG = "search query must be empty"

def a() -> None:
    raise ValueError(_MSG)
"""


def _seed(tmp_path: Path, rel: str, body: str) -> Path:
    p = tmp_path / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body, encoding="utf-8")
    return p


def test_detection_core_flags_duplicate(tmp_path: Path) -> None:
    p = _seed(tmp_path, "dup.py", _DUP)
    assert module_has_duplicate(p, min_length=10, min_occurrences=3) is True


def test_detection_core_clean(tmp_path: Path) -> None:
    p = _seed(tmp_path, "clean.py", _CLEAN)
    assert module_has_duplicate(p, min_length=10, min_occurrences=3) is False


def test_docstring_not_counted(tmp_path: Path) -> None:
    body = '"""this is a long module docstring repeated"""\n' * 1  # single docstring
    p = _seed(tmp_path, "d.py", body)
    assert module_has_duplicate(p, min_length=10, min_occurrences=3) is False


def test_threshold_is_config_driven(tmp_path: Path) -> None:
    p = _seed(tmp_path, "two.py", 'a="abcdefghij"\nb="abcdefghij"\n')
    # default 3 occurrences → clean; lower to 2 via config → violation.
    assert module_has_duplicate(p, min_length=10, min_occurrences=3) is False
    rule = build({"roots": ["."], "min_occurrences": 2}, repo_root=tmp_path)
    assert rule.file_has_violation(p) is True


def test_rule_from_config_scopes_roots(tmp_path: Path) -> None:
    _seed(tmp_path, "src/dup.py", _DUP)
    _seed(tmp_path, "vendor/dup.py", _DUP)
    rule = NoDuplicateString.from_config({"roots": ["src"]}, repo_root=tmp_path)
    assert {str(p) for p in rule.collect_violations()} == {"src/dup.py"}


def test_run_fails_on_new_then_establish_grandfathers(tmp_path: Path) -> None:
    _seed(tmp_path, "src/dup.py", _DUP)
    rule = NoDuplicateString.from_config({"roots": ["src"]}, repo_root=tmp_path)
    assert rule.run() == 1
    rule.establish_baseline()
    assert rule.run() == 0


def test_main_establish_baseline_mode(tmp_path: Path) -> None:
    _seed(tmp_path, "dup.py", _DUP)
    # main() uses default roots () → matches all .py via extension; scope to repo.
    rc = main(["--establish-baseline", "--repo-root", str(tmp_path)])
    assert rc == 0
    assert (tmp_path / ".architecture" / "baseline" / "no-duplicate-string-files.txt").exists()


def test_no_repo_strings_in_executable_code() -> None:
    # DESIGN LAW: a CORE module's LOGIC carries no repo identity (no taz/kairix
    # paths, globs, or thresholds). Provenance docstrings/comments may name the
    # donor repo, so this strips comments + docstrings via AST and scans only
    # the executable string literals + identifiers.
    import ast

    import tc_fitness.core_checks.no_duplicate_string as mod

    text = Path(mod.__file__).read_text(encoding="utf-8")  # type: ignore[arg-type]
    tree = ast.parse(text)
    docstring_node_ids = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Module | ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            doc = ast.get_docstring(node, clean=False)
            if doc is not None and node.body:
                first = node.body[0]
                if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant):
                    docstring_node_ids.add(id(first.value))

    repo_tokens = ("kairix", "tc-agent-zone", "agent-zone", "kata")
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if id(node) in docstring_node_ids:
                continue
            lowered = node.value.lower()
            for tok in repo_tokens:
                assert tok not in lowered, f"repo identity leaked in a code literal: {tok}"
