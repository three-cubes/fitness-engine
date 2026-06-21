"""Tests for the CORE check cognitive_complexity (v0.6.0)."""

from __future__ import annotations

import ast
from pathlib import Path

from tc_fitness.core_checks.cognitive_complexity import (
    CognitiveComplexity,
    build,
    main,
    module_over_threshold,
)

# A deeply nested function: nested ifs inside a loop push the score well past 15.
_COMPLEX = """
def f(items):
    total = 0
    for a in items:
        if a > 0:
            if a > 1:
                if a > 2:
                    if a > 3:
                        if a > 4:
                            if a > 5:
                                if a > 6:
                                    total += a
    return total
"""

_SIMPLE = """
def f(items):
    return sum(a for a in items if a > 0)
"""


def _seed(tmp_path: Path, rel: str, body: str) -> Path:
    p = tmp_path / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body, encoding="utf-8")
    return p


def test_detection_core_flags_complex(tmp_path: Path) -> None:
    p = _seed(tmp_path, "c.py", _COMPLEX)
    assert module_over_threshold(p, threshold=15) is True


def test_detection_core_clean(tmp_path: Path) -> None:
    p = _seed(tmp_path, "s.py", _SIMPLE)
    assert module_over_threshold(p, threshold=15) is False


def test_threshold_is_config_driven(tmp_path: Path) -> None:
    # A moderately-branchy function: clean at 15, flagged when the ceiling is 1.
    body = "def f(x):\n    if x:\n        return 1\n    return 0\n"
    p = _seed(tmp_path, "m.py", body)
    assert module_over_threshold(p, threshold=15) is False
    rule = build({"roots": ["."], "threshold": 0}, repo_root=tmp_path)
    assert rule.file_has_violation(p) is True


def test_syntax_error_is_not_a_violation(tmp_path: Path) -> None:
    p = _seed(tmp_path, "bad.py", "def f(:\n")
    assert module_over_threshold(p, threshold=15) is False


def test_rule_from_config_scopes_roots(tmp_path: Path) -> None:
    _seed(tmp_path, "src/c.py", _COMPLEX)
    _seed(tmp_path, "vendor/c.py", _COMPLEX)
    rule = CognitiveComplexity.from_config({"roots": ["src"]}, repo_root=tmp_path)
    assert {str(p) for p in rule.collect_violations()} == {"src/c.py"}


def test_run_then_establish_grandfathers(tmp_path: Path) -> None:
    _seed(tmp_path, "src/c.py", _COMPLEX)
    rule = CognitiveComplexity.from_config({"roots": ["src"]}, repo_root=tmp_path)
    assert rule.run() == 1
    rule.establish_baseline()
    assert rule.run() == 0


def test_main_establish_baseline_mode(tmp_path: Path) -> None:
    _seed(tmp_path, "c.py", _COMPLEX)
    rc = main(["--establish-baseline", "--repo-root", str(tmp_path)])
    assert rc == 0
    assert (tmp_path / ".architecture" / "baseline" / "cognitive-complexity-files.txt").exists()


def test_no_repo_strings_in_executable_code() -> None:
    import tc_fitness.core_checks.cognitive_complexity as mod

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
