"""Tests for the CORE check every_test_has_tier_marker (v0.6.0)."""

from __future__ import annotations

from pathlib import Path

from _core_check_assertions import assert_no_repo_identity

from tc_fitness.core_checks.every_test_has_tier_marker import (
    EveryTestHasTierMarker,
    build,
    file_missing_tier_marker,
    main,
)

_UNTAGGED = """
def test_parser() -> None:
    assert True
"""

_MODULE_MARKER = """
import pytest

pytestmark = pytest.mark.unit

def test_parser() -> None:
    assert True
"""

_FUNCTION_MARKER = """
import pytest

@pytest.mark.contract
def test_parser() -> None:
    assert True
"""

_NO_TESTS = """
import pytest

@pytest.fixture
def thing() -> int:
    return 1
"""

_TIERS = frozenset({"unit", "contract", "e2e"})


def _seed(tmp_path: Path, rel: str, body: str) -> Path:
    p = tmp_path / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body, encoding="utf-8")
    return p


def test_untagged_is_violation(tmp_path: Path) -> None:
    p = _seed(tmp_path, "test_x.py", _UNTAGGED)
    assert file_missing_tier_marker(p, tiers=_TIERS) is True


def test_module_marker_passes(tmp_path: Path) -> None:
    p = _seed(tmp_path, "test_x.py", _MODULE_MARKER)
    assert file_missing_tier_marker(p, tiers=_TIERS) is False


def test_function_marker_passes(tmp_path: Path) -> None:
    p = _seed(tmp_path, "test_x.py", _FUNCTION_MARKER)
    assert file_missing_tier_marker(p, tiers=_TIERS) is False


def test_file_without_tests_passes(tmp_path: Path) -> None:
    p = _seed(tmp_path, "test_x.py", _NO_TESTS)
    assert file_missing_tier_marker(p, tiers=_TIERS) is False


def test_tier_vocabulary_is_config_driven(tmp_path: Path) -> None:
    body = "import pytest\n\n@pytest.mark.fast\ndef test_x() -> None:\n    assert True\n"
    _seed(tmp_path, "tests/test_x.py", body)
    default = EveryTestHasTierMarker.from_config({"roots": ["tests"]}, repo_root=tmp_path)
    assert {str(p) for p in default.collect_violations()} == {"tests/test_x.py"}
    custom = EveryTestHasTierMarker.from_config(
        {"roots": ["tests"], "tier_markers": ["fast", "slow"]}, repo_root=tmp_path
    )
    assert custom.collect_violations() == set()


def test_scope_skips_non_test_files_and_excluded_parts(tmp_path: Path) -> None:
    _seed(tmp_path, "tests/helpers.py", _UNTAGGED)  # not test_*
    _seed(tmp_path, "tests/fixtures/test_x.py", _UNTAGGED)  # excluded part
    _seed(tmp_path, "tests/test_real.py", _UNTAGGED)
    rule = EveryTestHasTierMarker.from_config({"roots": ["tests"]}, repo_root=tmp_path)
    assert {str(p) for p in rule.collect_violations()} == {"tests/test_real.py"}


def test_run_then_establish_grandfathers(tmp_path: Path) -> None:
    _seed(tmp_path, "tests/test_x.py", _UNTAGGED)
    rule = EveryTestHasTierMarker.from_config({"roots": ["tests"]}, repo_root=tmp_path)
    assert rule.run() == 1
    rule.establish_baseline()
    assert rule.run() == 0


def test_main_establish_baseline_mode(tmp_path: Path) -> None:
    _seed(tmp_path, "test_x.py", _UNTAGGED)
    rc = main(["--establish-baseline", "--repo-root", str(tmp_path)])
    assert rc == 0
    assert (tmp_path / ".architecture" / "baseline" / "every-test-has-tier-marker-files.txt").exists()


def test_build_returns_rule() -> None:
    assert isinstance(build({}), EveryTestHasTierMarker)


def test_no_repo_strings_in_executable_code() -> None:
    import tc_fitness.core_checks.every_test_has_tier_marker as mod

    assert_no_repo_identity(mod.__file__)
