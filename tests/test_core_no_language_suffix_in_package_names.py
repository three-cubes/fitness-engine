"""Tests for the CORE check no_language_suffix_in_package_names (v0.6.0)."""

from __future__ import annotations

import ast
from pathlib import Path

from tc_fitness.core_checks.no_language_suffix_in_package_names import (
    NoLanguageSuffixInPackageNames,
    build,
    main,
    name_has_language_suffix,
)


def _mkdir(tmp_path: Path, rel: str) -> Path:
    p = tmp_path / rel
    p.mkdir(parents=True, exist_ok=True)
    return p


def test_detection_flags_suffix() -> None:
    assert name_has_language_suffix("mcp-render-ts", suffixes=("-ts",)) is True


def test_detection_clean() -> None:
    assert name_has_language_suffix("mcp-render", suffixes=("-ts", "-py")) is False


def test_boundary_root_scan(tmp_path: Path) -> None:
    _mkdir(tmp_path, "tools/mcp/render-ts")
    _mkdir(tmp_path, "tools/mcp/render")
    rule = build({"boundary_roots": ["tools/mcp"]}, repo_root=tmp_path)
    assert {str(p) for p in rule.collect_violations()} == {"tools/mcp/render-ts"}


def test_no_roots_flags_nothing(tmp_path: Path) -> None:
    _mkdir(tmp_path, "tools/mcp/render-ts")
    rule = build({}, repo_root=tmp_path)
    assert rule.collect_violations() == set()


def test_marker_root_gates_on_marker_file(tmp_path: Path) -> None:
    # leaf with marker → scanned; leaf without → ignored.
    _mkdir(tmp_path, "skills/content/render-ts")
    (tmp_path / "skills/content/render-ts/SKILL.md").write_text("x\n", encoding="utf-8")
    _mkdir(tmp_path, "skills/content/draft-py")  # no SKILL.md
    rule = build({"marker_roots": ["skills"], "marker_file": "SKILL.md"}, repo_root=tmp_path)
    assert {str(p) for p in rule.collect_violations()} == {"skills/content/render-ts"}


def test_forbidden_suffixes_config_driven(tmp_path: Path) -> None:
    _mkdir(tmp_path, "pkgs/thing-rb")
    # default suffixes don't include -rb → clean; configure it → violation.
    default_rule = build({"boundary_roots": ["pkgs"]}, repo_root=tmp_path)
    assert default_rule.collect_violations() == set()
    rule = build({"boundary_roots": ["pkgs"], "forbidden_suffixes": ["-rb"]}, repo_root=tmp_path)
    assert {str(p) for p in rule.collect_violations()} == {"pkgs/thing-rb"}


def test_run_fails_then_establish_grandfathers(tmp_path: Path) -> None:
    _mkdir(tmp_path, "tools/mcp/render-ts")
    rule = NoLanguageSuffixInPackageNames.from_config({"boundary_roots": ["tools/mcp"]}, repo_root=tmp_path)
    assert rule.run() == 1
    rule.establish_baseline()
    assert rule.run() == 0


def test_main_establish_baseline_mode(tmp_path: Path) -> None:
    _mkdir(tmp_path, "tools/mcp/render-ts")
    rc = main(["--establish-baseline", "--repo-root", str(tmp_path)])
    assert rc == 0
    expected = tmp_path / ".architecture" / "baseline" / "no-language-suffix-in-package-names-files.txt"
    assert expected.exists()


def test_no_repo_strings_in_executable_code() -> None:
    import tc_fitness.core_checks.no_language_suffix_in_package_names as mod

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
