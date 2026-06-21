"""Tests for the CORE check schema_conformance (v0.6.0)."""

from __future__ import annotations

import ast
from pathlib import Path

from tc_fitness.core_checks.schema_conformance import (
    build,
    file_missing_required_keys,
    main,
)


def _seed(tmp_path: Path, rel: str, body: str) -> Path:
    p = tmp_path / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body, encoding="utf-8")
    return p


def test_missing_key_is_violation(tmp_path: Path) -> None:
    p = _seed(tmp_path, "a.yaml", "palette: blue\n")
    assert file_missing_required_keys(p, required_keys=("palette", "typeScale")) is True


def test_all_keys_present_clean(tmp_path: Path) -> None:
    p = _seed(tmp_path, "a.yaml", "palette: blue\ntypeScale: 1.2\n")
    assert file_missing_required_keys(p, required_keys=("palette", "typeScale")) is False


def test_non_mapping_is_violation(tmp_path: Path) -> None:
    p = _seed(tmp_path, "a.yaml", "- item1\n- item2\n")
    assert file_missing_required_keys(p, required_keys=("palette",)) is True


def test_empty_required_keys_always_clean(tmp_path: Path) -> None:
    p = _seed(tmp_path, "a.yaml", "anything: ok\n")
    assert file_missing_required_keys(p, required_keys=()) is False


def test_json_parses_via_yaml(tmp_path: Path) -> None:
    p = _seed(tmp_path, "a.json", '{"palette": "blue"}')
    assert file_missing_required_keys(p, required_keys=("palette",)) is False
    assert file_missing_required_keys(p, required_keys=("missing",)) is True


def test_rule_scopes_roots_and_keys(tmp_path: Path) -> None:
    _seed(tmp_path, "tokens/acme.yaml", "palette: blue\n")
    _seed(tmp_path, "vendor/other.yaml", "palette: blue\n")
    rule = build({"roots": ["tokens"], "required_keys": ["palette", "typeScale"]}, repo_root=tmp_path)
    assert {str(p) for p in rule.collect_violations()} == {"tokens/acme.yaml"}


def test_run_fails_then_establish_grandfathers(tmp_path: Path) -> None:
    _seed(tmp_path, "tokens/acme.yaml", "palette: blue\n")
    rule = build({"roots": ["tokens"], "required_keys": ["palette", "typeScale"]}, repo_root=tmp_path)
    assert rule.run() == 1
    rule.establish_baseline()
    assert rule.run() == 0


def test_main_establish_baseline_mode(tmp_path: Path) -> None:
    _seed(tmp_path, "tokens/acme.yaml", "palette: blue\n")
    rc = main(["--establish-baseline", "--repo-root", str(tmp_path)])
    assert rc == 0
    assert (tmp_path / ".architecture" / "baseline" / "schema-conformance-files.txt").exists()


def test_no_repo_strings_in_executable_code() -> None:
    import tc_fitness.core_checks.schema_conformance as mod

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
