"""Tests for the CORE check no_internal_monkeypatch (v0.6.0)."""

from __future__ import annotations

from pathlib import Path

from _core_check_assertions import assert_no_repo_identity

from tc_fitness.core_checks.no_internal_monkeypatch import (
    NoInternalMonkeypatch,
    build,
    file_has_internal_patch,
    main,
)

_PATCH_DECORATOR = """
from unittest.mock import patch

@patch("myapp.core.search.run")
def test_x(mock_run):
    assert True
"""

_PATCH_STDLIB = """
from unittest.mock import patch

@patch("os.environ")
def test_x(mock_env):
    assert True
"""

_MONKEYPATCH_REF = """
import myapp.paths as paths_mod

def test_x(monkeypatch):
    monkeypatch.setattr(paths_mod, "provider_name", lambda: "fake")
"""

_FROZEN_RAISES = """
import pytest
import myapp.config as cfg

def test_x():
    with pytest.raises(Exception):
        cfg.value = 3
"""

_PKGS = ("myapp",)
_EXEMPT = frozenset({"os", "sys", "httpx"})


def _seed(tmp_path: Path, rel: str, body: str) -> Path:
    p = tmp_path / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body, encoding="utf-8")
    return p


def test_patch_decorator_on_internal_flagged(tmp_path: Path) -> None:
    p = _seed(tmp_path, "test_x.py", _PATCH_DECORATOR)
    assert file_has_internal_patch(p, internal_packages=_PKGS, exempt_roots=_EXEMPT) is True


def test_patch_on_stdlib_exempt(tmp_path: Path) -> None:
    p = _seed(tmp_path, "test_x.py", _PATCH_STDLIB)
    assert file_has_internal_patch(p, internal_packages=_PKGS, exempt_roots=_EXEMPT) is False


def test_monkeypatch_setattr_ref_flagged(tmp_path: Path) -> None:
    p = _seed(tmp_path, "test_x.py", _MONKEYPATCH_REF)
    assert file_has_internal_patch(p, internal_packages=_PKGS, exempt_roots=_EXEMPT) is True


def test_assignment_in_pytest_raises_is_exempt(tmp_path: Path) -> None:
    p = _seed(tmp_path, "test_x.py", _FROZEN_RAISES)
    assert file_has_internal_patch(p, internal_packages=_PKGS, exempt_roots=_EXEMPT) is False


def test_no_internal_packages_matches_nothing(tmp_path: Path) -> None:
    p = _seed(tmp_path, "test_x.py", _PATCH_DECORATOR)
    assert file_has_internal_patch(p, internal_packages=(), exempt_roots=_EXEMPT) is False


def test_packages_are_config_driven(tmp_path: Path) -> None:
    _seed(tmp_path, "tests/test_x.py", _PATCH_DECORATOR)
    rule = NoInternalMonkeypatch.from_config(
        {"roots": ["tests"], "internal_packages": ["myapp"], "exempt_roots": ["os"]},
        repo_root=tmp_path,
    )
    assert {str(p) for p in rule.collect_violations()} == {"tests/test_x.py"}


def test_run_then_establish_grandfathers(tmp_path: Path) -> None:
    _seed(tmp_path, "tests/test_x.py", _PATCH_DECORATOR)
    rule = NoInternalMonkeypatch.from_config(
        {"roots": ["tests"], "internal_packages": ["myapp"]}, repo_root=tmp_path
    )
    assert rule.run() == 1
    rule.establish_baseline()
    assert rule.run() == 0


def test_main_establish_baseline_mode(tmp_path: Path) -> None:
    _seed(tmp_path, "test_x.py", _PATCH_DECORATOR)
    rc = main(["--establish-baseline", "--repo-root", str(tmp_path)])
    assert rc == 0
    assert (tmp_path / ".architecture" / "baseline" / "no-internal-monkeypatch-files.txt").exists()


def test_build_returns_rule() -> None:
    assert isinstance(build({}), NoInternalMonkeypatch)


def test_no_repo_strings_in_executable_code() -> None:
    import tc_fitness.core_checks.no_internal_monkeypatch as mod

    assert_no_repo_identity(mod.__file__)
