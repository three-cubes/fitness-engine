"""Tests for the CORE check readme_resolver_coverage (v0.6.0)."""

from __future__ import annotations

import ast
from pathlib import Path

from tc_fitness.core_checks.readme_resolver_coverage import (
    ReadmeResolverCoverage,
    build,
    directory_missing_resolver,
    main,
)


def _mkdir(tmp_path: Path, rel: str) -> Path:
    p = tmp_path / rel
    p.mkdir(parents=True, exist_ok=True)
    return p


def _with_readme(tmp_path: Path, rel: str) -> Path:
    d = _mkdir(tmp_path, rel)
    (d / "README.md").write_text("resolver\n", encoding="utf-8")
    return d


def test_detection_missing(tmp_path: Path) -> None:
    d = _mkdir(tmp_path, "platform")
    assert directory_missing_resolver(d, resolver_file="README.md") is True


def test_detection_present(tmp_path: Path) -> None:
    d = _with_readme(tmp_path, "platform")
    assert directory_missing_resolver(d, resolver_file="README.md") is False


def test_top_level_scan_flags_missing(tmp_path: Path) -> None:
    _mkdir(tmp_path, "platform")  # no README
    _with_readme(tmp_path, "docs")
    rule = build({}, repo_root=tmp_path)
    assert {str(p) for p in rule.collect_violations()} == {"platform"}


def test_exempt_dir_skipped(tmp_path: Path) -> None:
    _mkdir(tmp_path, "logs")  # in default exempt_dirs
    _mkdir(tmp_path, "platform")
    rule = build({}, repo_root=tmp_path)
    assert {str(p) for p in rule.collect_violations()} == {"platform"}


def test_hidden_dir_skipped(tmp_path: Path) -> None:
    _mkdir(tmp_path, ".github")
    _mkdir(tmp_path, "platform")
    rule = build({}, repo_root=tmp_path)
    assert {str(p) for p in rule.collect_violations()} == {"platform"}


def test_resolver_file_config_driven(tmp_path: Path) -> None:
    d = _mkdir(tmp_path, "platform")
    (d / "INDEX.md").write_text("x\n", encoding="utf-8")
    # default README.md missing → violation; configure INDEX.md → clean.
    assert build({}, repo_root=tmp_path).collect_violations() == {Path("platform")}
    rule = build({"resolver_file": "INDEX.md"}, repo_root=tmp_path)
    assert rule.collect_violations() == set()


def test_exempt_dirs_config_driven(tmp_path: Path) -> None:
    _mkdir(tmp_path, "scratch")
    rule = build({"exempt_dirs": ["scratch"]}, repo_root=tmp_path)
    assert rule.collect_violations() == set()


def test_run_fails_then_establish_grandfathers(tmp_path: Path) -> None:
    _mkdir(tmp_path, "platform")
    rule = ReadmeResolverCoverage.from_config({}, repo_root=tmp_path)
    assert rule.run() == 1
    rule.establish_baseline()
    assert rule.run() == 0


def test_main_establish_baseline_mode(tmp_path: Path) -> None:
    _mkdir(tmp_path, "platform")
    rc = main(["--establish-baseline", "--repo-root", str(tmp_path)])
    assert rc == 0
    assert (tmp_path / ".architecture" / "baseline" / "readme-resolver-coverage-files.txt").exists()


def test_no_repo_strings_in_executable_code() -> None:
    import tc_fitness.core_checks.readme_resolver_coverage as mod

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
