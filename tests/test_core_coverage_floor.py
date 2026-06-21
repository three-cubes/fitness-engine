"""Tests for the CORE check coverage_floor (v0.6.0)."""

from __future__ import annotations

import ast
from pathlib import Path

from tc_fitness.core_checks.coverage_floor import (
    build,
    main,
    parse_coverage_report,
)


def _report(line_rates: dict[str, float], *, source: str = "src") -> str:
    classes = "\n".join(f'<class filename="{name}" line-rate="{rate}"/>' for name, rate in line_rates.items())
    return (
        '<?xml version="1.0" ?>\n'
        '<coverage line-rate="0.5">\n'
        f"  <sources><source>{source}</source></sources>\n"
        f"  <packages><package><classes>\n{classes}\n"
        "  </classes></package></packages>\n"
        "</coverage>\n"
    )


def _seed(tmp_path: Path, rel: str, body: str) -> Path:
    p = tmp_path / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body, encoding="utf-8")
    return p


def test_parse_joins_source_root(tmp_path: Path) -> None:
    p = _seed(tmp_path, "coverage.xml", _report({"a.py": 0.4, "b.py": 1.0}))
    parsed = parse_coverage_report(p)
    assert parsed == {"src/a.py": 40.0, "src/b.py": 100.0}


def test_parse_missing_report_empty(tmp_path: Path) -> None:
    assert parse_coverage_report(tmp_path / "nope.xml") == {}


def test_below_floor_is_violation(tmp_path: Path) -> None:
    _seed(tmp_path, "coverage.xml", _report({"a.py": 0.4, "b.py": 0.95}))
    rule = build({"roots": ["src"], "floor_pct": 90.0}, repo_root=tmp_path)
    assert {str(p) for p in rule.collect_violations()} == {"src/a.py"}


def test_floor_is_config_driven(tmp_path: Path) -> None:
    _seed(tmp_path, "coverage.xml", _report({"a.py": 0.85}))
    # floor 90 → a.py violates; floor 80 → clean.
    assert {
        str(p) for p in build({"roots": ["src"], "floor_pct": 90.0}, repo_root=tmp_path).collect_violations()
    } == {"src/a.py"}
    assert build({"roots": ["src"], "floor_pct": 80.0}, repo_root=tmp_path).collect_violations() == set()


def test_roots_scope_the_violation_set(tmp_path: Path) -> None:
    _seed(tmp_path, "coverage.xml", _report({"a.py": 0.1}, source="vendor"))
    rule = build({"roots": ["src"], "floor_pct": 90.0}, repo_root=tmp_path)
    assert rule.collect_violations() == set()  # vendor/a.py is out of the src root


def test_run_fails_then_establish_grandfathers(tmp_path: Path) -> None:
    _seed(tmp_path, "coverage.xml", _report({"a.py": 0.1}))
    rule = build({"roots": ["src"], "floor_pct": 90.0}, repo_root=tmp_path)
    assert rule.run() == 1
    rule.establish_baseline()
    assert rule.run() == 0


def test_unsafe_xml_rejected(tmp_path: Path) -> None:
    p = _seed(tmp_path, "coverage.xml", "<!DOCTYPE x>\n<coverage/>")
    try:
        parse_coverage_report(p)
    except ValueError as exc:
        assert "DTD/entity" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("expected ValueError for DTD declaration")


def test_main_establish_baseline_mode(tmp_path: Path) -> None:
    _seed(tmp_path, "coverage.xml", _report({"a.py": 0.1}))
    rc = main(["--establish-baseline", "--repo-root", str(tmp_path)])
    assert rc == 0
    assert (tmp_path / ".architecture" / "baseline" / "coverage-floor-files.txt").exists()


def test_no_repo_strings_in_executable_code() -> None:
    import tc_fitness.core_checks.coverage_floor as mod

    _assert_no_repo_identity(Path(mod.__file__))


def _assert_no_repo_identity(module_file: Path) -> None:
    text = module_file.read_text(encoding="utf-8")
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
