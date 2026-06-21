"""Tests for the CORE check no_test_imports_in_prod (v0.6.0)."""

from __future__ import annotations

from pathlib import Path

from _core_check_assertions import assert_no_repo_identity

from tc_fitness.core_checks.no_test_imports_in_prod import (
    NoTestImportsInProd,
    build,
    file_imports_test_tree,
    main,
)

_FROM_IMPORT = "from tests.fakes import FakeRepo\n"
_BARE_IMPORT = "import tests\n"
_CLEAN = "from myapp.core.null import NullRepo\n"


def _seed(tmp_path: Path, rel: str, body: str) -> Path:
    p = tmp_path / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body, encoding="utf-8")
    return p


def test_from_import_flagged(tmp_path: Path) -> None:
    p = _seed(tmp_path, "m.py", _FROM_IMPORT)
    assert file_imports_test_tree(p, forbidden_roots=("tests",)) is True


def test_bare_import_flagged(tmp_path: Path) -> None:
    p = _seed(tmp_path, "m.py", _BARE_IMPORT)
    assert file_imports_test_tree(p, forbidden_roots=("tests",)) is True


def test_clean(tmp_path: Path) -> None:
    p = _seed(tmp_path, "m.py", _CLEAN)
    assert file_imports_test_tree(p, forbidden_roots=("tests",)) is False


def test_forbidden_root_is_config_driven(tmp_path: Path) -> None:
    _seed(tmp_path, "src/m.py", "from spec_tests.x import Y\n")
    default = NoTestImportsInProd.from_config({"roots": ["src"]}, repo_root=tmp_path)
    assert default.collect_violations() == set()  # default root is `tests`
    custom = NoTestImportsInProd.from_config(
        {"roots": ["src"], "forbidden_import_roots": ["spec_tests"]}, repo_root=tmp_path
    )
    assert {str(p) for p in custom.collect_violations()} == {"src/m.py"}


def test_rule_from_config_scopes_roots(tmp_path: Path) -> None:
    _seed(tmp_path, "src/m.py", _FROM_IMPORT)
    _seed(tmp_path, "tests/test_m.py", _FROM_IMPORT)  # not under prod root
    rule = NoTestImportsInProd.from_config({"roots": ["src"]}, repo_root=tmp_path)
    assert {str(p) for p in rule.collect_violations()} == {"src/m.py"}


def test_run_then_establish_grandfathers(tmp_path: Path) -> None:
    _seed(tmp_path, "src/m.py", _FROM_IMPORT)
    rule = NoTestImportsInProd.from_config({"roots": ["src"]}, repo_root=tmp_path)
    assert rule.run() == 1
    rule.establish_baseline()
    assert rule.run() == 0


def test_main_establish_baseline_mode(tmp_path: Path) -> None:
    _seed(tmp_path, "m.py", _FROM_IMPORT)
    rc = main(["--establish-baseline", "--repo-root", str(tmp_path)])
    assert rc == 0
    assert (tmp_path / ".architecture" / "baseline" / "no-test-imports-in-prod-files.txt").exists()


def test_build_returns_rule() -> None:
    assert isinstance(build({}), NoTestImportsInProd)


def test_no_repo_strings_in_executable_code() -> None:
    import tc_fitness.core_checks.no_test_imports_in_prod as mod

    assert_no_repo_identity(mod.__file__)
