"""Tests for the CORE check suppressions_have_rationale (v0.6.0)."""

from __future__ import annotations

import ast
import re
from pathlib import Path

from tc_fitness.core_checks.suppressions_have_rationale import (
    DEFAULT_BARE_PATTERNS,
    SuppressionsHaveRationale,
    build,
    file_has_bare_suppression,
    main,
)

_BARE = "x = 1  # NOSONAR\n"
_WITH_REASON = "x = 1  # NOSONAR - internal log path; not user-controlled\n"
_BARE_NOQA = "y = call()  # noqa: BLE001\n"

_COMPILED = tuple(re.compile(p) for p in DEFAULT_BARE_PATTERNS)


def _seed(tmp_path: Path, rel: str, body: str) -> Path:
    p = tmp_path / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body, encoding="utf-8")
    return p


def test_detection_core_flags_bare(tmp_path: Path) -> None:
    p = _seed(tmp_path, "b.py", _BARE)
    assert file_has_bare_suppression(p, _COMPILED) is True


def test_bare_noqa_with_code_flagged(tmp_path: Path) -> None:
    p = _seed(tmp_path, "n.py", _BARE_NOQA)
    assert file_has_bare_suppression(p, _COMPILED) is True


def test_rationale_satisfies(tmp_path: Path) -> None:
    p = _seed(tmp_path, "r.py", _WITH_REASON)
    assert file_has_bare_suppression(p, _COMPILED) is False


def test_bare_patterns_config_driven(tmp_path: Path) -> None:
    # A consumer-specific bare token.
    p = _seed(tmp_path, "src/c.py", "z = 1  # SILENCE\n")
    rule = build({"roots": ["src"], "bare_patterns": [r"#\s*SILENCE\s*$"]}, repo_root=tmp_path)
    assert rule.file_has_violation(p) is True


def test_rule_from_config_scopes_roots(tmp_path: Path) -> None:
    _seed(tmp_path, "src/b.py", _BARE)
    _seed(tmp_path, "vendor/b.py", _BARE)
    rule = SuppressionsHaveRationale.from_config({"roots": ["src"]}, repo_root=tmp_path)
    assert {str(p) for p in rule.collect_violations()} == {"src/b.py"}


def test_run_then_establish_grandfathers(tmp_path: Path) -> None:
    _seed(tmp_path, "src/b.py", _BARE)
    rule = SuppressionsHaveRationale.from_config({"roots": ["src"]}, repo_root=tmp_path)
    assert rule.run() == 1
    rule.establish_baseline()
    assert rule.run() == 0


def test_main_establish_baseline_mode(tmp_path: Path) -> None:
    _seed(tmp_path, "b.py", _BARE)
    rc = main(["--establish-baseline", "--repo-root", str(tmp_path)])
    assert rc == 0
    assert (tmp_path / ".architecture" / "baseline" / "suppressions-have-rationale-files.txt").exists()


def test_no_repo_strings_in_executable_code() -> None:
    import tc_fitness.core_checks.suppressions_have_rationale as mod

    text = Path(mod.__file__).read_text(encoding="utf-8")
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
            for tok in ("kairix", "tc-agent-zone", "agent-zone", "kata"):
                assert tok not in lowered, f"repo identity leaked: {tok}"
