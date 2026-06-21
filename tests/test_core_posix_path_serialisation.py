"""Tests for the CORE check posix_path_serialisation (v0.6.0)."""

from __future__ import annotations

import ast
from pathlib import Path

from tc_fitness.core_checks.posix_path_serialisation import (
    PosixPathSerialisation,
    build,
    main,
    module_has_os_native_serialisation,
)

_BAD = "rel = str(path.relative_to(root))\n"
_OK_AS_POSIX = "rel = path.relative_to(root).as_posix()\n"
_OK_REDUNDANT = "rel = str(path.relative_to(root).as_posix())\n"
_OK_NO_RELATIVE = "rel = str(some_path)\n"


def _seed(tmp_path: Path, rel: str, body: str) -> Path:
    p = tmp_path / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body, encoding="utf-8")
    return p


def test_detection_flags_os_native(tmp_path: Path) -> None:
    p = _seed(tmp_path, "bad.py", _BAD)
    assert module_has_os_native_serialisation(p) is True


def test_detection_clean_as_posix(tmp_path: Path) -> None:
    p = _seed(tmp_path, "ok.py", _OK_AS_POSIX)
    assert module_has_os_native_serialisation(p) is False


def test_detection_clean_redundant_as_posix(tmp_path: Path) -> None:
    p = _seed(tmp_path, "ok2.py", _OK_REDUNDANT)
    assert module_has_os_native_serialisation(p) is False


def test_detection_clean_str_without_relative_to(tmp_path: Path) -> None:
    p = _seed(tmp_path, "ok3.py", _OK_NO_RELATIVE)
    assert module_has_os_native_serialisation(p) is False


def test_syntax_error_is_not_a_violation(tmp_path: Path) -> None:
    p = _seed(tmp_path, "broken.py", "def (:\n")
    assert module_has_os_native_serialisation(p) is False


def test_excluded_segment_is_config_driven(tmp_path: Path) -> None:
    _seed(tmp_path, "src/bad.py", _BAD)
    _seed(tmp_path, "src/tests/bad.py", _BAD)
    rule = build({"roots": ["src"]}, repo_root=tmp_path)
    # Default excluded_segments includes "tests" → only src/bad.py flagged.
    assert {str(p) for p in rule.collect_violations()} == {"src/bad.py"}


def test_excluded_segments_overridable(tmp_path: Path) -> None:
    _seed(tmp_path, "src/vendor/bad.py", _BAD)
    rule = build({"roots": ["src"], "excluded_segments": ["vendor"]}, repo_root=tmp_path)
    assert rule.collect_violations() == set()


def test_rule_from_config_scopes_roots(tmp_path: Path) -> None:
    _seed(tmp_path, "src/bad.py", _BAD)
    _seed(tmp_path, "vendor/bad.py", _BAD)
    rule = PosixPathSerialisation.from_config({"roots": ["src"]}, repo_root=tmp_path)
    assert {str(p) for p in rule.collect_violations()} == {"src/bad.py"}


def test_run_fails_then_establish_grandfathers(tmp_path: Path) -> None:
    _seed(tmp_path, "src/bad.py", _BAD)
    rule = PosixPathSerialisation.from_config({"roots": ["src"]}, repo_root=tmp_path)
    assert rule.run() == 1
    rule.establish_baseline()
    assert rule.run() == 0


def test_main_establish_baseline_mode(tmp_path: Path) -> None:
    _seed(tmp_path, "bad.py", _BAD)
    rc = main(["--establish-baseline", "--repo-root", str(tmp_path)])
    assert rc == 0
    assert (tmp_path / ".architecture" / "baseline" / "posix-path-serialisation-files.txt").exists()


def test_no_repo_strings_in_executable_code() -> None:
    import tc_fitness.core_checks.posix_path_serialisation as mod

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
