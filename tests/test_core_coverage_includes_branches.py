"""Tests for the CORE check coverage_includes_branches (v0.6.0)."""

from __future__ import annotations

import ast
from pathlib import Path

from tc_fitness.core_checks.coverage_includes_branches import (
    build,
    main,
    report_lacks_branches,
)


def _seed(tmp_path: Path, body: str) -> Path:
    p = tmp_path / "coverage.xml"
    p.write_text(body, encoding="utf-8")
    return p


_BRANCH_AWARE = '<coverage line-rate="0.9" branch-rate="0.38" branches-valid="3070" branches-covered="1196"/>'
_LINES_ONLY = '<coverage line-rate="0.9" branch-rate="0" branches-valid="0"/>'


def test_lines_only_report_violates(tmp_path: Path) -> None:
    p = _seed(tmp_path, _LINES_ONLY)
    assert report_lacks_branches(p) is True


def test_branch_aware_report_clean(tmp_path: Path) -> None:
    p = _seed(tmp_path, _BRANCH_AWARE)
    assert report_lacks_branches(p) is False


def test_missing_report_not_a_violation(tmp_path: Path) -> None:
    assert report_lacks_branches(tmp_path / "absent.xml") is False


def test_rule_flags_lines_only(tmp_path: Path) -> None:
    _seed(tmp_path, _LINES_ONLY)
    rule = build({}, repo_root=tmp_path)
    assert {str(p) for p in rule.collect_violations()} == {"coverage.xml"}


def test_rule_clean_on_branch_aware(tmp_path: Path) -> None:
    _seed(tmp_path, _BRANCH_AWARE)
    assert build({}, repo_root=tmp_path).collect_violations() == set()


def test_report_path_is_config_driven(tmp_path: Path) -> None:
    nested = tmp_path / "build" / "cov.xml"
    nested.parent.mkdir(parents=True)
    nested.write_text(_LINES_ONLY, encoding="utf-8")
    rule = build({"coverage_report": "build/cov.xml"}, repo_root=tmp_path)
    assert rule.run() == 1


def test_run_passes_when_no_report(tmp_path: Path) -> None:
    assert build({}, repo_root=tmp_path).run() == 0


def test_unsafe_xml_rejected(tmp_path: Path) -> None:
    p = _seed(tmp_path, "<!ENTITY x>\n<coverage/>")
    try:
        report_lacks_branches(p)
    except ValueError as exc:
        assert "DTD/entity" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("expected ValueError for ENTITY declaration")


def test_main_runs(tmp_path: Path) -> None:
    _seed(tmp_path, _BRANCH_AWARE)
    assert main(["--repo-root", str(tmp_path)]) == 0


def test_no_repo_strings_in_executable_code() -> None:
    import tc_fitness.core_checks.coverage_includes_branches as mod

    text = Path(mod.__file__).read_text(encoding="utf-8")
    tree = ast.parse(text)
    docstring_ids = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Module | ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef) and node.body:
            first = node.body[0]
            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant):
                docstring_ids.add(id(first.value))
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docstring_ids:
            lowered = node.value.lower()
            for tok in ("kairix", "tc-agent-zone", "agent-zone", "kata"):
                assert tok not in lowered, f"repo identity leaked in a code literal: {tok}"
