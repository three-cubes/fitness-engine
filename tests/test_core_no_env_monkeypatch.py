"""Tests for the CORE check no_env_monkeypatch (v0.6.0)."""

from __future__ import annotations

from pathlib import Path

from _core_check_assertions import assert_no_repo_identity

from tc_fitness.core_checks.no_env_monkeypatch import (
    NoEnvMonkeypatch,
    build,
    file_has_env_monkeypatch,
    main,
)

_SETENV = """
def test_x(monkeypatch, tmp_path):
    monkeypatch.setenv("MYAPP_DATA_DIR", str(tmp_path))
"""

_OTHER_ENV = """
def test_x(monkeypatch):
    monkeypatch.setenv("PATH", "/usr/bin")
"""

_CLEAN = """
def test_x(tmp_path):
    paths = FakePaths(data_dir=tmp_path)
    assert paths
"""


def _seed(tmp_path: Path, rel: str, body: str) -> Path:
    p = tmp_path / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body, encoding="utf-8")
    return p


def test_detection_flags_owned_prefix(tmp_path: Path) -> None:
    p = _seed(tmp_path, "test_x.py", _SETENV)
    assert file_has_env_monkeypatch(p, prefixes=("MYAPP_",)) is True


def test_detection_ignores_unowned_key(tmp_path: Path) -> None:
    p = _seed(tmp_path, "test_x.py", _OTHER_ENV)
    assert file_has_env_monkeypatch(p, prefixes=("MYAPP_",)) is False


def test_clean(tmp_path: Path) -> None:
    p = _seed(tmp_path, "test_x.py", _CLEAN)
    assert file_has_env_monkeypatch(p, prefixes=("MYAPP_",)) is False


def test_no_prefixes_matches_nothing(tmp_path: Path) -> None:
    p = _seed(tmp_path, "test_x.py", _SETENV)
    assert file_has_env_monkeypatch(p, prefixes=()) is False


def test_prefix_is_config_driven(tmp_path: Path) -> None:
    _seed(tmp_path, "tests/test_x.py", _SETENV)
    rule = NoEnvMonkeypatch.from_config({"roots": ["tests"], "env_prefixes": ["MYAPP_"]}, repo_root=tmp_path)
    assert {str(p) for p in rule.collect_violations()} == {"tests/test_x.py"}


def test_run_then_establish_grandfathers(tmp_path: Path) -> None:
    _seed(tmp_path, "tests/test_x.py", _SETENV)
    rule = NoEnvMonkeypatch.from_config({"roots": ["tests"], "env_prefixes": ["MYAPP_"]}, repo_root=tmp_path)
    assert rule.run() == 1
    rule.establish_baseline()
    assert rule.run() == 0


def test_main_establish_baseline_mode(tmp_path: Path) -> None:
    _seed(tmp_path, "test_x.py", _SETENV)
    rc = main(["--establish-baseline", "--repo-root", str(tmp_path)])
    assert rc == 0
    assert (tmp_path / ".architecture" / "baseline" / "no-env-monkeypatch-files.txt").exists()


def test_build_returns_rule() -> None:
    assert isinstance(build({}), NoEnvMonkeypatch)


def test_no_repo_strings_in_executable_code() -> None:
    import tc_fitness.core_checks.no_env_monkeypatch as mod

    assert_no_repo_identity(mod.__file__)
