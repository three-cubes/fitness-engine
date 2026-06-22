"""Tests for the configurable branch-naming engine gate (Task 1.8).

Lifts taz's ``scripts/checks/branch_naming.py`` (Linear ``gitBranchName`` shape
``<user>/<team>-<number>-<slug>``) into the shared engine with the exempt sets
as config — taz keeps ``develop`` in its exempt branches, kairix doesn't. The
branch name is injected (no git dependency in the unit tests); the default
``DEFAULT_LINEAR_PATTERN`` is the Linear shape.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

from tc_fitness.checks.branch_naming import (
    DEFAULT_EXEMPT_PATTERNS,
    DEFAULT_LINEAR_PATTERN,
    GITHUB_HEAD_REF_ENV,
    check_branch,
    current_branch,
)


def test_linear_shape_passes() -> None:
    rc = check_branch("dan/kno-45-pr-a-sync-compose")
    assert rc == 0


def test_non_linear_shape_fails() -> None:
    rc = check_branch("feature/random-thing")  # no team-number segment
    assert rc == 1


def test_default_exempt_branches_main() -> None:
    assert check_branch("main") == 0
    assert check_branch("HEAD") == 0


def test_develop_not_exempt_by_default_but_configurable() -> None:
    # kairix: develop is NOT exempt → fails as a non-conforming name.
    assert check_branch("develop") == 1
    # taz: develop added to the exempt set → passes.
    assert check_branch("develop", exempt_branches={"main", "develop", "HEAD"}) == 0


def test_exempt_patterns_default_cover_automation() -> None:
    assert check_branch("worktree-agent-abc123") == 0
    assert check_branch("agent/sgo106-tc-fitness") == 0  # autonomous-agent PR branches
    assert check_branch("renovate/some-dep") == 0
    assert check_branch("dependabot/pip/foo") == 0
    assert check_branch("gh-pages") == 0


def test_custom_exempt_patterns_extend() -> None:
    extra = (*DEFAULT_EXEMPT_PATTERNS, re.compile(r"^release/"))
    assert check_branch("release/2026.6", exempt_patterns=extra) == 0
    # Without the extra pattern it fails.
    assert check_branch("release/2026.6") == 1


def test_custom_pattern_overrides_shape() -> None:
    # A repo with a different convention passes its own compiled pattern.
    custom = re.compile(r"^wip-\d+$")
    assert check_branch("wip-7", pattern=custom) == 0
    assert check_branch("dan/kno-45-slug", pattern=custom) == 1


def test_fail_prints_remediation(capsys: pytest.CaptureFixture[str]) -> None:
    rc = check_branch("nope")
    err = capsys.readouterr().err
    assert rc == 1
    assert "nope" in err
    assert "fix:" in err or "rename" in err.lower()


def test_default_pattern_is_the_linear_shape() -> None:
    assert DEFAULT_LINEAR_PATTERN.match("dan/kno-45-slug")
    assert not DEFAULT_LINEAR_PATTERN.match("main")


def test_none_branch_skips_clean(capsys: pytest.CaptureFixture[str]) -> None:
    # Not in a git repo / detached → skip clean (exit 0), never a false fail.
    rc = check_branch(None)
    assert rc == 0


# ── PR-event detached-HEAD resolution (the gate must BITE on PRs) ────────────
#
# On a GitHub ``pull_request`` event the runner checks out the merge commit in
# DETACHED HEAD, so ``git rev-parse --abbrev-ref HEAD`` returns the literal
# ``"HEAD"`` (which is exempt) and the branch-naming rule would silently no-op.
# ``current_branch`` must resolve ``$GITHUB_HEAD_REF`` first so the gate checks
# the ACTUAL PR source-branch name. These tests inject ``env`` + a tmp git repo
# explicitly — no real environment mutation, no network, fully deterministic.


def _init_detached_repo(tmp_path: Path) -> Path:
    """A throwaway git repo left in DETACHED HEAD — the PR-checkout shape.

    Reproduces what a GitHub ``pull_request`` checkout looks like locally:
    ``git rev-parse --abbrev-ref HEAD`` resolves to the literal ``"HEAD"``.
    """
    run = lambda *a: subprocess.run(["git", *a], cwd=tmp_path, check=True, capture_output=True)  # noqa: E731
    run("init", "-q")
    run("config", "user.email", "t@example.com")
    run("config", "user.name", "t")
    run("commit", "--allow-empty", "-q", "-m", "c0")
    sha = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=tmp_path, text=True).strip()
    run("checkout", "-q", sha)  # detach HEAD onto the commit SHA
    abbrev = subprocess.check_output(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=tmp_path, text=True
    ).strip()
    assert abbrev == "HEAD", f"expected detached HEAD, got {abbrev!r}"
    return tmp_path


def test_pr_event_resolves_head_ref_over_detached_head(tmp_path: Path) -> None:
    # On a PR event the env var wins over the detached `git` "HEAD" — the gate
    # sees the real source-branch name, not the exempt literal "HEAD".
    repo = _init_detached_repo(tmp_path)
    branch = current_branch(repo, env={GITHUB_HEAD_REF_ENV: "feature/random-thing"})
    assert branch == "feature/random-thing"


def test_pr_event_bad_branch_name_now_FAILS() -> None:
    # The regression the bug masked: a non-conforming PR branch name must FAIL,
    # not silently pass as the exempt "HEAD" did before the fix.
    branch = current_branch(env={GITHUB_HEAD_REF_ENV: "feature/random-thing"})
    assert branch == "feature/random-thing"
    assert check_branch(branch) == 1


def test_pr_event_good_branch_name_PASSES() -> None:
    # A Linear-shaped PR branch name passes on a PR event.
    branch = current_branch(env={GITHUB_HEAD_REF_ENV: "dan/sgo-106-tc-fitness"})
    assert branch == "dan/sgo-106-tc-fitness"
    assert check_branch(branch) == 0


def test_detached_head_without_pr_ref_skips_clean(tmp_path: Path) -> None:
    # Detached HEAD and NO PR env var (e.g. a local detached checkout) → the
    # literal "HEAD" is mapped to None (a clean skip), never a false failure.
    repo = _init_detached_repo(tmp_path)
    assert current_branch(repo, env={}) is None
    assert check_branch(current_branch(repo, env={})) == 0


def test_empty_head_ref_falls_back_to_git(tmp_path: Path) -> None:
    # GITHUB_HEAD_REF is set but EMPTY on non-PR events (push). It must be
    # ignored so the real branch name is resolved from git.
    run = lambda *a: subprocess.run(["git", *a], cwd=tmp_path, check=True, capture_output=True)  # noqa: E731
    run("init", "-q")
    run("config", "user.email", "t@example.com")
    run("config", "user.name", "t")
    run("commit", "--allow-empty", "-q", "-m", "c0")
    run("checkout", "-q", "-b", "dan/sgo-106-real-branch")
    branch = current_branch(tmp_path, env={GITHUB_HEAD_REF_ENV: ""})
    assert branch == "dan/sgo-106-real-branch"


def test_push_event_uses_git_branch(tmp_path: Path) -> None:
    # No PR env var at all → the local/push branch name comes from git.
    run = lambda *a: subprocess.run(["git", *a], cwd=tmp_path, check=True, capture_output=True)  # noqa: E731
    run("init", "-q")
    run("config", "user.email", "t@example.com")
    run("config", "user.name", "t")
    run("commit", "--allow-empty", "-q", "-m", "c0")
    run("checkout", "-q", "-b", "feature/not-linear")
    branch = current_branch(tmp_path, env={})
    assert branch == "feature/not-linear"
    assert check_branch(branch) == 1  # the gate bites on push too
