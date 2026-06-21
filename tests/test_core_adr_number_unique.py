"""Tests for the CORE check adr_number_unique (v0.6.0 security-freshness batch)."""

from __future__ import annotations

import re
from pathlib import Path

from tc_fitness.core_checks.adr_number_unique import (
    DEFAULT_RECORD_PATTERN,
    AdrNumberUnique,
    build,
    find_collisions,
    main,
)

_PATTERN = re.compile(DEFAULT_RECORD_PATTERN)


def _seed(tmp_path: Path, rel: str, body: str = "x\n") -> Path:
    p = tmp_path / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body, encoding="utf-8")
    return p


def test_collision_detected(tmp_path: Path) -> None:
    d = tmp_path / "docs" / "decisions"
    _seed(tmp_path, "docs/decisions/ADR-041-foo.md")
    _seed(tmp_path, "docs/decisions/ADR-041-bar.md")
    collisions = find_collisions(d, pattern=_PATTERN)
    assert set(collisions) == {"041"}
    assert len(collisions["041"]) == 2


def test_unique_numbers_no_collision(tmp_path: Path) -> None:
    d = tmp_path / "docs" / "decisions"
    _seed(tmp_path, "docs/decisions/ADR-041-foo.md")
    _seed(tmp_path, "docs/decisions/ADR-042-bar.md")
    assert find_collisions(d, pattern=_PATTERN) == {}


def test_collect_violations_returns_colliding_files(tmp_path: Path) -> None:
    _seed(tmp_path, "docs/decisions/ADR-041-foo.md")
    _seed(tmp_path, "docs/decisions/ADR-041-bar.md")
    _seed(tmp_path, "docs/decisions/ADR-042-ok.md")
    rule = build({}, repo_root=tmp_path)
    rels = {str(p) for p in rule.collect_violations()}
    assert rels == {"docs/decisions/ADR-041-foo.md", "docs/decisions/ADR-041-bar.md"}


def test_custom_dir_and_pattern_via_config(tmp_path: Path) -> None:
    _seed(tmp_path, "rfc/RFC-7-a.md")
    _seed(tmp_path, "rfc/RFC-7-b.md")
    rule = build({"record_dir": "rfc", "record_pattern": r"^RFC-(\d+)-.+\.md$"}, repo_root=tmp_path)
    assert len(rule.collect_violations()) == 2


def test_run_fails_then_establish_grandfathers(tmp_path: Path) -> None:
    _seed(tmp_path, "docs/decisions/ADR-041-foo.md")
    _seed(tmp_path, "docs/decisions/ADR-041-bar.md")
    rule = AdrNumberUnique.from_config({}, repo_root=tmp_path)
    assert rule.run() == 1
    rule.establish_baseline()
    assert rule.run() == 0


def test_main_establish_baseline_mode(tmp_path: Path) -> None:
    _seed(tmp_path, "docs/decisions/ADR-041-foo.md")
    _seed(tmp_path, "docs/decisions/ADR-041-bar.md")
    rc = main(["--establish-baseline", "--repo-root", str(tmp_path)])
    assert rc == 0
    assert (tmp_path / ".architecture" / "baseline" / "adr-number-unique-files.txt").exists()
