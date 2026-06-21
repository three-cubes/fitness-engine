"""Tests for the keystone drift-enders (v0.6.0)."""

from __future__ import annotations

import subprocess
from pathlib import Path

from tc_fitness.baseline import establish_baseline
from tc_fitness.keystone import (
    baseline_shrink_only,
    catalogue_check_consistency,
    find_net_new_violations,
    load_all_baselines,
    net_new_violations_forbidden,
    reconcile_catalogue,
    resolve_previous_tag,
)


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


def _init_repo(tmp_path: Path) -> Path:
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "t@example.com")
    _git(tmp_path, "config", "user.name", "Test")
    return tmp_path


# ── net_new_violations_forbidden ──────────────────────────────────────────


def test_load_all_baselines(tmp_path: Path) -> None:
    establish_baseline("rule-a", ["src/x.py"], tmp_path)
    establish_baseline("rule-b", ["src/y.py"], tmp_path)
    loaded = load_all_baselines(tmp_path)
    assert loaded["rule-a-files.txt"] == {"src/x.py"}
    assert loaded["rule-b-files.txt"] == {"src/y.py"}


def test_find_net_new_hits() -> None:
    baselines = {"r-files.txt": {"src/old.py"}}
    assert find_net_new_violations(["src/old.py"], baselines) == {"r-files.txt": ["src/old.py"]}
    assert find_net_new_violations(["src/new.py"], baselines) == {}


def test_net_new_violations_forbidden_clean(tmp_path: Path) -> None:
    establish_baseline("r", ["src/old.py"], tmp_path)
    assert net_new_violations_forbidden(["src/brand-new.py"], tmp_path, print_fn=lambda _m: None) == 0


def test_net_new_violations_forbidden_blocks_grandfathered_add(tmp_path: Path) -> None:
    establish_baseline("r", ["src/old.py"], tmp_path)
    # An ADDED file that is already in the baseline → fail.
    assert net_new_violations_forbidden(["src/old.py"], tmp_path, print_fn=lambda _m: None) == 1


# ── baseline_shrink_only ──────────────────────────────────────────────────


def test_shrink_only_first_release_skips(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    rc = baseline_shrink_only(["a-files.txt"], tmp_path, print_fn=lambda _m: None)
    assert rc == 0  # no prior tag → clean skip


def test_shrink_only_passes_when_baseline_shrinks(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    rel = ".architecture/baseline/r-files.txt"
    establish_baseline("r", ["a", "b", "c"], repo)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "seed")
    _git(repo, "tag", "v0.1.0")
    # Pay one entry down.
    establish_baseline("r", ["a", "b"], repo)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "shrink")
    assert baseline_shrink_only([rel], repo, print_fn=lambda _m: None) == 0


def test_shrink_only_fails_when_baseline_grows(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    rel = ".architecture/baseline/r-files.txt"
    establish_baseline("r", ["a"], repo)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "seed")
    _git(repo, "tag", "v0.1.0")
    establish_baseline("r", ["a", "b"], repo)  # grew
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "grow")
    assert baseline_shrink_only([rel], repo, print_fn=lambda _m: None) == 1


def test_shrink_only_fails_when_baseline_stalls_above_zero(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    rel = ".architecture/baseline/r-files.txt"
    establish_baseline("r", ["a", "b"], repo)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "seed")
    _git(repo, "tag", "v0.1.0")
    _seed = repo / "x.txt"
    _seed.write_text("x")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "no baseline change")
    assert baseline_shrink_only([rel], repo, print_fn=lambda _m: None) == 1


def test_resolve_previous_tag(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    (repo / "a.txt").write_text("a")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "c1")
    _git(repo, "tag", "v0.1.0")
    (repo / "b.txt").write_text("b")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "c2")
    assert resolve_previous_tag(repo) == "v0.1.0"


# ── catalogue_check_consistency ───────────────────────────────────────────


def test_reconcile_clean() -> None:
    report = reconcile_catalogue(
        cataloged_check_ids=["core:a", "core:b"],
        available_check_ids=["core:a", "core:b"],
    )
    assert report.ok


def test_reconcile_orphan_check() -> None:
    report = reconcile_catalogue(
        cataloged_check_ids=["core:a"],
        available_check_ids=["core:a", "core:b"],
    )
    assert report.orphan_checks == ["core:b"]
    assert not report.ok


def test_reconcile_dangling_entry() -> None:
    report = reconcile_catalogue(
        cataloged_check_ids=["core:a", "core:missing"],
        available_check_ids=["core:a"],
    )
    assert report.dangling_entries == [("core:missing", "core:missing")]
    assert not report.ok


def test_catalogue_check_consistency_exit_codes() -> None:
    ok = catalogue_check_consistency(
        cataloged_check_ids=["x"],
        available_check_ids=["x"],
        print_fn=lambda _m: None,
    )
    assert ok == 0
    bad = catalogue_check_consistency(
        cataloged_check_ids=["x", "y"],
        available_check_ids=["x"],
        print_fn=lambda _m: None,
    )
    assert bad == 1
