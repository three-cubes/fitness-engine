"""Tests for the CORE check no_hardcoded_repo_paths (v0.6.0)."""

from __future__ import annotations

import ast
from pathlib import Path

from tc_fitness.core_checks.no_hardcoded_repo_paths import (
    NoHardcodedRepoPaths,
    build,
    file_contains_needle,
    main,
)

_NEEDLE = "/data/development/myrepo/"
_BAD = f'ROOT = "{_NEEDLE}"\n'
_OK = "ROOT = Path(__file__).resolve().parents[2]\n"


def _seed(tmp_path: Path, rel: str, body: str) -> Path:
    p = tmp_path / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body, encoding="utf-8")
    return p


def test_detection_flags_needle(tmp_path: Path) -> None:
    p = _seed(tmp_path, "bad.py", _BAD)
    assert file_contains_needle(p, needles=(_NEEDLE,)) is True


def test_detection_clean(tmp_path: Path) -> None:
    p = _seed(tmp_path, "ok.py", _OK)
    assert file_contains_needle(p, needles=(_NEEDLE,)) is False


def test_empty_needles_flags_nothing(tmp_path: Path) -> None:
    p = _seed(tmp_path, "bad.py", _BAD)
    assert file_contains_needle(p, needles=()) is False


def test_no_needles_configured_is_clean(tmp_path: Path) -> None:
    _seed(tmp_path, "src/bad.py", _BAD)
    rule = build({"roots": ["src"]}, repo_root=tmp_path)
    assert rule.collect_violations() == set()


def test_needle_from_config(tmp_path: Path) -> None:
    _seed(tmp_path, "src/bad.py", _BAD)
    _seed(tmp_path, "src/ok.py", _OK)
    rule = build({"roots": ["src"], "needles": [_NEEDLE]}, repo_root=tmp_path)
    assert {str(p) for p in rule.collect_violations()} == {"src/bad.py"}


def test_markdown_exempt_by_default(tmp_path: Path) -> None:
    _seed(tmp_path, "src/doc.md", _BAD)
    rule = build(
        {"roots": ["src"], "needles": [_NEEDLE], "extensions": [".md"]},
        repo_root=tmp_path,
    )
    # .md is in exempt_extensions → dropped even though extensions allows it.
    assert rule.collect_violations() == set()


def test_exempt_prefix_from_config(tmp_path: Path) -> None:
    _seed(tmp_path, "src/host/run.py", _BAD)
    _seed(tmp_path, "src/app/run.py", _BAD)
    rule = build(
        {"roots": ["src"], "needles": [_NEEDLE], "exempt_prefixes": ["src/host/"]},
        repo_root=tmp_path,
    )
    assert {str(p) for p in rule.collect_violations()} == {"src/app/run.py"}


def test_run_fails_then_establish_grandfathers(tmp_path: Path) -> None:
    _seed(tmp_path, "src/bad.py", _BAD)
    rule = NoHardcodedRepoPaths.from_config({"roots": ["src"], "needles": [_NEEDLE]}, repo_root=tmp_path)
    assert rule.run() == 1
    rule.establish_baseline()
    assert rule.run() == 0


def test_main_establish_baseline_mode(tmp_path: Path) -> None:
    _seed(tmp_path, "bad.py", _BAD)
    rc = main(["--establish-baseline", "--repo-root", str(tmp_path)])
    assert rc == 0
    assert (tmp_path / ".architecture" / "baseline" / "no-hardcoded-repo-paths-files.txt").exists()


def test_no_repo_strings_in_executable_code() -> None:
    import tc_fitness.core_checks.no_hardcoded_repo_paths as mod

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
