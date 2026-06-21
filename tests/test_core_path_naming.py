"""Tests for the CORE check path_naming (v0.6.0)."""

from __future__ import annotations

import ast
from pathlib import Path

from tc_fitness.core_checks.path_naming import (
    PathNaming,
    build,
    main,
    name_violates_convention,
)


def _seed(tmp_path: Path, rel: str, body: str = "x\n") -> Path:
    p = tmp_path / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body, encoding="utf-8")
    return p


def test_detection_bad_kebab_md() -> None:
    assert (
        name_violates_convention(
            "docs/MyNote.md",
            kebab_roots=("docs/",),
            snake_roots=(),
            allowed_names=frozenset(),
        )
        is True
    )


def test_detection_good_kebab_md() -> None:
    assert (
        name_violates_convention(
            "docs/my-note.md",
            kebab_roots=("docs/",),
            snake_roots=(),
            allowed_names=frozenset(),
        )
        is False
    )


def test_detection_bad_snake_py() -> None:
    assert (
        name_violates_convention(
            "scripts/My-Check.py",
            kebab_roots=(),
            snake_roots=("scripts/",),
            allowed_names=frozenset(),
        )
        is True
    )


def test_detection_good_snake_py() -> None:
    assert (
        name_violates_convention(
            "scripts/my_check.py",
            kebab_roots=(),
            snake_roots=("scripts/",),
            allowed_names=frozenset(),
        )
        is False
    )


def test_allowed_name_exempt() -> None:
    assert (
        name_violates_convention(
            "docs/README.md",
            kebab_roots=("docs/",),
            snake_roots=(),
            allowed_names=frozenset({"README.md"}),
        )
        is False
    )


def test_path_under_no_root_is_clean() -> None:
    assert (
        name_violates_convention(
            "vendor/BadName.md",
            kebab_roots=("docs/",),
            snake_roots=(),
            allowed_names=frozenset(),
        )
        is False
    )


def test_rule_from_config_scopes_roots(tmp_path: Path) -> None:
    _seed(tmp_path, "docs/BadNote.md")
    _seed(tmp_path, "docs/good-note.md")
    _seed(tmp_path, "vendor/BadNote.md")
    rule = build({"kebab_roots": ["docs/"]}, repo_root=tmp_path)
    assert {str(p) for p in rule.collect_violations()} == {"docs/BadNote.md"}


def test_no_roots_flags_nothing(tmp_path: Path) -> None:
    _seed(tmp_path, "docs/BadNote.md")
    rule = build({}, repo_root=tmp_path)
    assert rule.collect_violations() == set()


def test_snake_root_init_allowed(tmp_path: Path) -> None:
    _seed(tmp_path, "scripts/__init__.py")
    _seed(tmp_path, "scripts/_private_helper.py")
    rule = build({"snake_roots": ["scripts/"]}, repo_root=tmp_path)
    assert rule.collect_violations() == set()


def test_run_fails_then_establish_grandfathers(tmp_path: Path) -> None:
    _seed(tmp_path, "docs/BadNote.md")
    rule = PathNaming.from_config({"kebab_roots": ["docs/"]}, repo_root=tmp_path)
    assert rule.run() == 1
    rule.establish_baseline()
    assert rule.run() == 0


def test_main_establish_baseline_mode(tmp_path: Path) -> None:
    _seed(tmp_path, "docs/BadNote.md")
    rc = main(["--establish-baseline", "--repo-root", str(tmp_path)])
    assert rc == 0
    assert (tmp_path / ".architecture" / "baseline" / "path-naming-files.txt").exists()


def test_no_repo_strings_in_executable_code() -> None:
    import tc_fitness.core_checks.path_naming as mod

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
