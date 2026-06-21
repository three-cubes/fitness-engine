"""Tests for the CORE check ci_fanin_parity (v0.6.0)."""

from __future__ import annotations

import ast
from pathlib import Path

from tc_fitness.core_checks.ci_fanin_parity import (
    CiFaninParity,
    build,
    main,
    workflow_fanin_is_dishonest,
)

_HONEST = """
name: ci
on: [push]
jobs:
  unit:
    name: "Unit tests"
    runs-on: ubuntu-latest
  security:
    name: "Security scan"
    runs-on: ubuntu-latest
  check:
    name: "CI gate"
    needs: [unit, security]
    runs-on: ubuntu-latest
"""

_DANGLING = """
name: ci
on: [push]
jobs:
  unit:
    name: "Unit tests"
    runs-on: ubuntu-latest
  license-scan:
    name: "License scan"
    runs-on: ubuntu-latest
  check:
    name: "CI gate"
    needs: [unit]
    runs-on: ubuntu-latest
"""

_MARKED = """
name: ci
on: [push]
jobs:
  unit:
    name: "Unit tests"
    runs-on: ubuntu-latest
  # fan-in: informational - advisory, posts a PR comment
  sonar:
    name: "Sonar scan"
    runs-on: ubuntu-latest
  check:
    name: "CI gate"
    needs: [unit]
    runs-on: ubuntu-latest
"""

_NO_AGGREGATOR = """
name: ci
on: [push]
jobs:
  unit:
    name: "Unit tests"
    runs-on: ubuntu-latest
"""


def _seed(tmp_path: Path, rel: str, body: str) -> Path:
    p = tmp_path / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body, encoding="utf-8")
    return p


def test_honest_fanin_clean(tmp_path: Path) -> None:
    p = _seed(tmp_path, "ci.yml", _HONEST)
    assert (
        workflow_fanin_is_dishonest(
            p, aggregator_name="CI gate", informational_marker="# fan-in: informational"
        )
        is False
    )


def test_dangling_job_flagged(tmp_path: Path) -> None:
    p = _seed(tmp_path, "ci.yml", _DANGLING)
    assert (
        workflow_fanin_is_dishonest(
            p, aggregator_name="CI gate", informational_marker="# fan-in: informational"
        )
        is True
    )


def test_marked_informational_clean(tmp_path: Path) -> None:
    p = _seed(tmp_path, "ci.yml", _MARKED)
    assert (
        workflow_fanin_is_dishonest(
            p, aggregator_name="CI gate", informational_marker="# fan-in: informational"
        )
        is False
    )


def test_missing_aggregator_flagged(tmp_path: Path) -> None:
    p = _seed(tmp_path, "ci.yml", _NO_AGGREGATOR)
    assert (
        workflow_fanin_is_dishonest(
            p, aggregator_name="CI gate", informational_marker="# fan-in: informational"
        )
        is True
    )


def test_aggregator_name_config_driven(tmp_path: Path) -> None:
    body = _DANGLING.replace("CI gate", "merge-gate")
    _seed(tmp_path, ".github/workflows/ci.yml", body)
    rule = build({"aggregator_name": "merge-gate"}, repo_root=tmp_path)
    assert {str(p) for p in rule.collect_violations()} == {".github/workflows/ci.yml"}


def test_rule_clean_on_honest(tmp_path: Path) -> None:
    _seed(tmp_path, ".github/workflows/ci.yml", _HONEST)
    rule = build({}, repo_root=tmp_path)
    assert rule.collect_violations() == set()


def test_absent_workflow_is_clean(tmp_path: Path) -> None:
    rule = build({}, repo_root=tmp_path)
    assert rule.collect_violations() == set()


def test_run_fails_then_establish_grandfathers(tmp_path: Path) -> None:
    _seed(tmp_path, ".github/workflows/ci.yml", _DANGLING)
    rule = CiFaninParity.from_config({}, repo_root=tmp_path)
    assert rule.run() == 1
    rule.establish_baseline()
    assert rule.run() == 0


def test_main_establish_baseline_mode(tmp_path: Path) -> None:
    _seed(tmp_path, ".github/workflows/ci.yml", _DANGLING)
    rc = main(["--establish-baseline", "--repo-root", str(tmp_path)])
    assert rc == 0
    assert (tmp_path / ".architecture" / "baseline" / "ci-fanin-parity-files.txt").exists()


def test_no_repo_strings_in_executable_code() -> None:
    import tc_fitness.core_checks.ci_fanin_parity as mod

    text = Path(mod.__file__).read_text(encoding="utf-8")
    tree = ast.parse(text)
    docstring_ids = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Module | ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            doc = ast.get_docstring(node, clean=False)
            if doc is not None and node.body:
                first = node.body[0]
                if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant):
                    docstring_ids.add(id(first.value))
    repo_tokens = ("kairix", "tc-agent-zone", "agent-zone", "kata")
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if id(node) in docstring_ids:
                continue
            lowered = node.value.lower()
            for tok in repo_tokens:
                assert tok not in lowered, f"repo identity leaked in a code literal: {tok}"
