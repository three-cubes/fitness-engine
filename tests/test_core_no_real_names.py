"""Tests for the CORE check no_real_names (v0.6.0 security-freshness batch)."""

from __future__ import annotations

from pathlib import Path

from tc_fitness.core_checks.no_real_names import NoRealNames, build, file_has_real_name, main


def _seed(tmp_path: Path, rel: str, body: str) -> Path:
    p = tmp_path / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body, encoding="utf-8")
    return p


def test_detection_flags_banned_token(tmp_path: Path) -> None:
    p = _seed(tmp_path, "examples/case.md", "client is AcmeCorp here")
    assert (
        file_has_real_name(p, tokens=["AcmeCorp"], scope_segments=["examples"], repo_root=tmp_path.resolve())
        is True
    )


def test_word_boundary_no_false_positive(tmp_path: Path) -> None:
    p = _seed(tmp_path, "examples/case.md", "AcmeCorporation is fine")
    assert (
        file_has_real_name(p, tokens=["AcmeCorp"], scope_segments=["examples"], repo_root=tmp_path.resolve())
        is False
    )


def test_digit_bearing_token_matches(tmp_path: Path) -> None:
    p = _seed(tmp_path, "examples/case.md", "engaged by 3CV last year")
    assert (
        file_has_real_name(p, tokens=["3CV"], scope_segments=["examples"], repo_root=tmp_path.resolve())
        is True
    )


def test_scope_segments_narrow_scan(tmp_path: Path) -> None:
    rule = build({"roots": ["."], "tokens": ["AcmeCorp"], "scope_segments": ["examples"]}, repo_root=tmp_path)
    in_scope = _seed(tmp_path, "examples/a.md", "AcmeCorp")
    out_scope = _seed(tmp_path, "src/b.md", "AcmeCorp")
    assert rule.file_has_violation(in_scope) is True
    assert rule.file_has_violation(out_scope) is False


def test_substitutions_map_supplies_tokens(tmp_path: Path) -> None:
    # A {token: substitute} map is accepted; the detector keys on the token set.
    rule = build(
        {"roots": ["."], "substitutions": {"AcmeCorp": "SynthCo"}, "scope_segments": []}, repo_root=tmp_path
    )
    p = _seed(tmp_path, "examples/a.md", "AcmeCorp")
    assert rule.file_has_violation(p) is True


def test_empty_tokens_is_noop(tmp_path: Path) -> None:
    rule = build({"roots": ["."]}, repo_root=tmp_path)
    p = _seed(tmp_path, "examples/a.md", "anything at all")
    assert rule.file_has_violation(p) is False


def test_run_fails_then_establish_grandfathers(tmp_path: Path) -> None:
    _seed(tmp_path, "examples/a.md", "AcmeCorp")
    rule = NoRealNames.from_config(
        {"roots": ["examples"], "tokens": ["AcmeCorp"], "extensions": [".md"]}, repo_root=tmp_path
    )
    assert rule.run() == 1
    rule.establish_baseline()
    assert rule.run() == 0


def test_main_establish_baseline_mode(tmp_path: Path) -> None:
    _seed(tmp_path, "examples/a.md", "AcmeCorp")
    rc = main(["--establish-baseline", "--repo-root", str(tmp_path)])
    assert rc == 0
    assert (tmp_path / ".architecture" / "baseline" / "no-real-names-files.txt").exists()
