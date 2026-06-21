"""Tests for the CORE check no_test_only_kwargs (v0.6.0)."""

from __future__ import annotations

from pathlib import Path

from _core_check_assertions import assert_no_repo_identity

from tc_fitness.core_checks.no_test_only_kwargs import (
    NoTestOnlyKwargs,
    build,
    find_test_only_kwargs_in_file,
    main,
)

_SEAM = """
def route(intent, clock_fn=None):
    return intent
"""

_CLEAN = """
def route(intent, deps=None):
    return intent
"""

_METHOD_EXEMPT = """
class RouterDeps:
    def __init__(self, clock_fn=None):
        self.clock_fn = clock_fn
"""


def _seed(tmp_path: Path, rel: str, body: str) -> Path:
    p = tmp_path / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body, encoding="utf-8")
    return p


def test_detection_flags_free_function_seam(tmp_path: Path) -> None:
    p = _seed(tmp_path, "router.py", _SEAM)
    found = find_test_only_kwargs_in_file(p, suffixes=("_fn",))
    assert found == [("route", "clock_fn", 2)]


def test_detection_clean(tmp_path: Path) -> None:
    p = _seed(tmp_path, "router.py", _CLEAN)
    assert find_test_only_kwargs_in_file(p, suffixes=("_fn",)) == []


def test_methods_on_class_are_exempt(tmp_path: Path) -> None:
    p = _seed(tmp_path, "deps.py", _METHOD_EXEMPT)
    assert find_test_only_kwargs_in_file(p, suffixes=("_fn",)) == []


def test_suffixes_are_config_driven(tmp_path: Path) -> None:
    body = "def make(store_loader=None):\n    return store_loader\n"
    _seed(tmp_path, "scripts/m.py", body)
    default = NoTestOnlyKwargs.from_config({"roots": ["scripts"]}, repo_root=tmp_path)
    assert default.collect_violations() == set()  # only _fn by default
    custom = NoTestOnlyKwargs.from_config(
        {"roots": ["scripts"], "seam_suffixes": ["_fn", "_loader"]}, repo_root=tmp_path
    )
    assert {str(p) for p in custom.collect_violations()} == {"scripts/m.py"}


def test_exempt_keys_allow_documented_seam(tmp_path: Path) -> None:
    _seed(tmp_path, "scripts/router.py", _SEAM)
    rule = NoTestOnlyKwargs.from_config(
        {"roots": ["scripts"], "exempt_keys": ["scripts/router.py::route::clock_fn"]},
        repo_root=tmp_path,
    )
    assert rule.collect_violations() == set()


def test_rule_from_config_scopes_roots(tmp_path: Path) -> None:
    _seed(tmp_path, "scripts/router.py", _SEAM)
    _seed(tmp_path, "vendor/router.py", _SEAM)
    rule = NoTestOnlyKwargs.from_config({"roots": ["scripts"]}, repo_root=tmp_path)
    assert {str(p) for p in rule.collect_violations()} == {"scripts/router.py"}


def test_run_then_establish_grandfathers(tmp_path: Path) -> None:
    _seed(tmp_path, "scripts/router.py", _SEAM)
    rule = NoTestOnlyKwargs.from_config({"roots": ["scripts"]}, repo_root=tmp_path)
    assert rule.run() == 1
    rule.establish_baseline()
    assert rule.run() == 0


def test_main_establish_baseline_mode(tmp_path: Path) -> None:
    _seed(tmp_path, "router.py", _SEAM)
    rc = main(["--establish-baseline", "--repo-root", str(tmp_path)])
    assert rc == 0
    assert (tmp_path / ".architecture" / "baseline" / "no-test-only-kwargs-files.txt").exists()


def test_build_returns_rule() -> None:
    assert isinstance(build({}), NoTestOnlyKwargs)


def test_no_repo_strings_in_executable_code() -> None:
    import tc_fitness.core_checks.no_test_only_kwargs as mod

    assert_no_repo_identity(mod.__file__)
