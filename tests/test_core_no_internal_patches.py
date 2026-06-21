"""Tests for the CORE check no_internal_patches (v0.6.0 security-freshness batch)."""

from __future__ import annotations

from pathlib import Path

from tc_fitness.core_checks.no_internal_patches import (
    NoInternalPatches,
    build,
    file_patches_internal,
    main,
)

_INTERNAL = frozenset({"scripts", "tools"})
_EXEMPT = frozenset({"os", "subprocess", "pytest"})


def _seed(tmp_path: Path, rel: str, body: str) -> Path:
    p = tmp_path / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body, encoding="utf-8")
    return p


def test_flags_monkeypatch_string_target(tmp_path: Path) -> None:
    p = _seed(tmp_path, "t.py", "def test_x(monkeypatch):\n    monkeypatch.setattr('scripts.a.b', 1)\n")
    assert file_patches_internal(p, internal_roots=_INTERNAL, exempt_roots=_EXEMPT) is True


def test_flags_patch_decorator(tmp_path: Path) -> None:
    p = _seed(
        tmp_path, "t.py", "from unittest.mock import patch\n@patch('tools.x.y')\ndef test_x():\n    pass\n"
    )
    assert file_patches_internal(p, internal_roots=_INTERNAL, exempt_roots=_EXEMPT) is True


def test_stdlib_boundary_is_clean(tmp_path: Path) -> None:
    p = _seed(tmp_path, "t.py", "def test_x(monkeypatch):\n    monkeypatch.setattr('os.environ', {})\n")
    assert file_patches_internal(p, internal_roots=_INTERNAL, exempt_roots=_EXEMPT) is False


def test_pytest_raises_assign_not_flagged(tmp_path: Path) -> None:
    body = "import scripts\ndef test_x():\n    with pytest.raises(ValueError):\n        scripts.attr = 1\n"
    p = _seed(tmp_path, "t.py", body)
    assert file_patches_internal(p, internal_roots=_INTERNAL, exempt_roots=_EXEMPT) is False


def test_empty_internal_roots_is_noop(tmp_path: Path) -> None:
    rule = build({"roots": ["."]}, repo_root=tmp_path)
    p = _seed(tmp_path, "t.py", "def test_x(monkeypatch):\n    monkeypatch.setattr('scripts.a.b', 1)\n")
    assert rule.file_has_violation(p) is False


def test_config_scopes_internal_roots(tmp_path: Path) -> None:
    rule = build(
        {"roots": ["tests"], "internal_roots": ["scripts"], "exempt_roots": ["os"]}, repo_root=tmp_path
    )
    _seed(tmp_path, "tests/t.py", "def test_x(monkeypatch):\n    monkeypatch.setattr('scripts.a.b', 1)\n")
    assert {str(x) for x in rule.collect_violations()} == {"tests/t.py"}


def test_run_fails_then_establish_grandfathers(tmp_path: Path) -> None:
    _seed(tmp_path, "tests/t.py", "def test_x(monkeypatch):\n    monkeypatch.setattr('scripts.a.b', 1)\n")
    rule = NoInternalPatches.from_config(
        {"roots": ["tests"], "internal_roots": ["scripts"]}, repo_root=tmp_path
    )
    assert rule.run() == 1
    rule.establish_baseline()
    assert rule.run() == 0


def test_main_establish_baseline_mode(tmp_path: Path) -> None:
    _seed(tmp_path, "t.py", "def test_x(monkeypatch):\n    monkeypatch.setattr('scripts.a.b', 1)\n")
    # internal_roots empty by default → no violations; establish writes empty baseline.
    rc = main(["--establish-baseline", "--repo-root", str(tmp_path)])
    assert rc == 0
    assert (tmp_path / ".architecture" / "baseline" / "no-internal-patches-files.txt").exists()
