"""Tests for the per-file baseline I/O (v0.6.0)."""

from __future__ import annotations

from pathlib import Path

from tc_fitness.baseline import (
    BASELINE_SUFFIX,
    baseline_path,
    establish_baseline,
    load_baseline,
    parse_baseline_text,
    render_baseline,
)


def test_baseline_path_is_canonical_suffix(tmp_path: Path) -> None:
    p = baseline_path("no-duplicate-string", tmp_path)
    assert p == tmp_path / ".architecture" / "baseline" / f"no-duplicate-string{BASELINE_SUFFIX}"


def test_parse_skips_comments_and_blanks() -> None:
    text = "# header\n\nsrc/a.py\n  src/b.py  \n# trailing comment\n"
    assert parse_baseline_text(text) == {"src/a.py", "src/b.py"}


def test_load_missing_baseline_is_empty(tmp_path: Path) -> None:
    assert load_baseline("nope", tmp_path) == set()


def test_render_has_mandatory_header_and_shrink_contract() -> None:
    text = render_baseline("my-rule", ["src/b.py", "src/a.py", "src/a.py"])
    assert text.startswith("# Baseline for fitness check: my-rule")
    assert "may only SHRINK" in text
    # entries de-duplicated + sorted
    assert text.rstrip().endswith("src/a.py\nsrc/b.py")


def test_establish_then_load_roundtrip(tmp_path: Path) -> None:
    written = establish_baseline("my-rule", {"src/x.py", "src/y.py"}, tmp_path)
    assert written.exists()
    assert load_baseline("my-rule", tmp_path) == {"src/x.py", "src/y.py"}


def test_establish_creates_baseline_dir(tmp_path: Path) -> None:
    assert not (tmp_path / ".architecture").exists()
    establish_baseline("r", [], tmp_path)
    assert (tmp_path / ".architecture" / "baseline").is_dir()


def test_establish_empty_writes_header_only(tmp_path: Path) -> None:
    establish_baseline("r", [], tmp_path)
    assert load_baseline("r", tmp_path) == set()


def test_render_reader_writer_agree(tmp_path: Path) -> None:
    establish_baseline("r", ["src/a.py"], tmp_path)
    raw = baseline_path("r", tmp_path).read_text()
    assert parse_baseline_text(raw) == load_baseline("r", tmp_path)
