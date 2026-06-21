"""Tests for the CORE check test_skip_rationale (v0.6.0)."""

from __future__ import annotations

from pathlib import Path

from _core_check_assertions import assert_no_repo_identity

from tc_fitness.core_checks.test_skip_rationale import (
    TestSkipRationale,
    build,
    file_has_skip_without_reason,
    main,
)

_BARE_SKIP = """
import pytest

@pytest.mark.skip
def test_a() -> None:
    assert True
"""

_SKIP_WITH_REASON = """
import pytest

@pytest.mark.skip(reason="re-enabled once the upstream fix lands")
def test_a() -> None:
    assert True
"""

_IMPORTORSKIP_NO_COMMENT = """
import pytest

docx = pytest.importorskip("docx")
"""

_IMPORTORSKIP_WITH_COMMENT = """
import pytest

# docx is an optional runtime dep — skip if missing
docx = pytest.importorskip("docx")
"""


def _seed(tmp_path: Path, rel: str, body: str) -> Path:
    p = tmp_path / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body, encoding="utf-8")
    return p


def test_detection_flags_bare_skip(tmp_path: Path) -> None:
    p = _seed(tmp_path, "test_x.py", _BARE_SKIP)
    assert file_has_skip_without_reason(p, importorskip_lookback=3) is True


def test_detection_passes_skip_with_reason(tmp_path: Path) -> None:
    p = _seed(tmp_path, "test_x.py", _SKIP_WITH_REASON)
    assert file_has_skip_without_reason(p, importorskip_lookback=3) is False


def test_importorskip_needs_comment(tmp_path: Path) -> None:
    bad = _seed(tmp_path, "test_bad.py", _IMPORTORSKIP_NO_COMMENT)
    good = _seed(tmp_path, "test_good.py", _IMPORTORSKIP_WITH_COMMENT)
    assert file_has_skip_without_reason(bad, importorskip_lookback=3) is True
    assert file_has_skip_without_reason(good, importorskip_lookback=3) is False


def test_lookback_is_config_driven(tmp_path: Path) -> None:
    # Comment two blank-free lines above; lookback=1 cannot see it, lookback=3 can.
    body = "import pytest\n# the reason\nx = 1\ndocx = pytest.importorskip('docx')\n"
    p = _seed(tmp_path, "test_lb.py", body)
    assert file_has_skip_without_reason(p, importorskip_lookback=1) is True
    assert file_has_skip_without_reason(p, importorskip_lookback=3) is False


def test_rule_from_config_scopes_roots(tmp_path: Path) -> None:
    _seed(tmp_path, "tests/test_a.py", _BARE_SKIP)
    _seed(tmp_path, "vendor/test_b.py", _BARE_SKIP)
    rule = TestSkipRationale.from_config({"roots": ["tests"]}, repo_root=tmp_path)
    assert {str(p) for p in rule.collect_violations()} == {"tests/test_a.py"}


def test_run_then_establish_grandfathers(tmp_path: Path) -> None:
    _seed(tmp_path, "tests/test_a.py", _BARE_SKIP)
    rule = TestSkipRationale.from_config({"roots": ["tests"]}, repo_root=tmp_path)
    assert rule.run() == 1
    rule.establish_baseline()
    assert rule.run() == 0


def test_main_establish_baseline_mode(tmp_path: Path) -> None:
    _seed(tmp_path, "test_a.py", _BARE_SKIP)
    rc = main(["--establish-baseline", "--repo-root", str(tmp_path)])
    assert rc == 0
    assert (tmp_path / ".architecture" / "baseline" / "test-skip-rationale-files.txt").exists()


def test_build_returns_rule() -> None:
    assert isinstance(build({}), TestSkipRationale)


def test_no_repo_strings_in_executable_code() -> None:
    import tc_fitness.core_checks.test_skip_rationale as mod

    assert_no_repo_identity(mod.__file__)
