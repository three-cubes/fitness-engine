"""Tests for the CORE check canonical_commit_identity (SP-A / SGO-158).

Gates the author + committer identity of every commit in a range against an
allowlist, bounded by an optional cutover ref (guard-forward, decision D2).
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from tc_fitness.core_checks.canonical_commit_identity import (
    CanonicalCommitIdentity,
    build,
    main,
)

BOT = "295831460+three-cubes-agent[bot]@users.noreply.github.com"
HUMAN = "dan@example.com"
ALLOW = {"allowed_emails": [BOT, HUMAN]}

# Platform committer identities exempted intrinsically by the check (SGO-198).
WEBFLOW_NAME = "GitHub"
WEBFLOW_EMAIL = "noreply@github.com"
DEPENDABOT_NAME = "dependabot[bot]"
DEPENDABOT_EMAIL = "49699333+dependabot[bot]@users.noreply.github.com"
RENOVATE_NAME = "renovate[bot]"
RENOVATE_EMAIL = "29139614+renovate[bot]@users.noreply.github.com"
# A real platform bot that is deliberately NOT exempted — workflow commits must
# still carry the canonical App identity, so this must still fail as a committer.
GHA_NAME = "github-actions[bot]"
GHA_EMAIL = "41898282+github-actions[bot]@users.noreply.github.com"
ROGUE_AUTHOR = "feat-156-deploy"
ROGUE_EMAIL = "noreply@anthropic.com"


def _git(
    repo: Path, *args: str, an: str = "Dev", ae: str = HUMAN, cn: str | None = None, ce: str | None = None
) -> None:
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": an,
        "GIT_AUTHOR_EMAIL": ae,
        "GIT_COMMITTER_NAME": cn if cn is not None else an,
        "GIT_COMMITTER_EMAIL": ce if ce is not None else ae,
    }
    subprocess.run(["git", *args], cwd=repo, env=env, check=True, capture_output=True, text=True)


def _init(tmp_path: Path) -> Path:
    repo = tmp_path
    _git(repo, "init", "-q")
    _git(repo, "config", "commit.gpgsign", "false")
    return repo


def _commit(
    repo: Path, msg: str, *, an: str = "Dan", ae: str = HUMAN, cn: str | None = None, ce: str | None = None
) -> str:
    (repo / "f.txt").write_text(msg, encoding="utf-8")
    _git(repo, "add", "-A", an=an, ae=ae, cn=cn, ce=ce)
    _git(repo, "commit", "-q", "-m", msg, an=an, ae=ae, cn=cn, ce=ce)
    return subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()


def test_empty_allowlist_is_noop(tmp_path: Path) -> None:
    repo = _init(tmp_path)
    _commit(repo, "base")
    _commit(repo, "rogue", an="feat-156-deploy", ae="noreply@anthropic.com")
    rule = build({"base_ref": "HEAD~1", "head_ref": "HEAD"}, repo_root=repo)
    assert rule.run() == 0  # no allowlist configured → no-op pass


def test_allowed_identities_pass(tmp_path: Path) -> None:
    repo = _init(tmp_path)
    base = _commit(repo, "base", ae=BOT, an="three-cubes-agent[bot]")
    _commit(repo, "work", ae=HUMAN, an="Dan")
    rule = build({**ALLOW, "base_ref": base, "head_ref": "HEAD"}, repo_root=repo)
    assert rule.run() == 0


def test_non_allowed_author_email_fails(tmp_path: Path) -> None:
    repo = _init(tmp_path)
    base = _commit(repo, "base", ae=HUMAN)
    _commit(repo, "rogue", an="feat-156-deploy", ae="noreply@anthropic.com")
    rule = build({**ALLOW, "base_ref": base, "head_ref": "HEAD"}, repo_root=repo)
    assert rule.run() == 1


def test_committer_distinct_from_author_is_checked(tmp_path: Path) -> None:
    repo = _init(tmp_path)
    base = _commit(repo, "base", ae=HUMAN)
    # author allowed, committer NOT allowed → still a violation.
    _commit(repo, "work", an="Dan", ae=HUMAN, cn="agent-zone-generator", ce="gen@three-cubes.local")
    rule = build({**ALLOW, "base_ref": base, "head_ref": "HEAD"}, repo_root=repo)
    assert rule.run() == 1


def test_emoji_in_name_fails_when_name_pattern_configured(tmp_path: Path) -> None:
    repo = _init(tmp_path)
    base = _commit(repo, "base", ae=HUMAN, an="Dan")
    _commit(repo, "work", an="Builder \U0001f528", ae=HUMAN)  # 🔨 in the name
    rule = build(
        {**ALLOW, "allowed_name_patterns": [r"^[\w .,'-]+$"], "base_ref": base, "head_ref": "HEAD"},
        repo_root=repo,
    )
    assert rule.run() == 1


def test_cutover_ref_grandfathers_prior_commits(tmp_path: Path) -> None:
    repo = _init(tmp_path)
    _commit(repo, "old-rogue", an="feat-156-deploy", ae="noreply@anthropic.com")
    cutover = _commit(repo, "cutover-line", ae=HUMAN, an="Dan")
    _commit(repo, "clean-after", ae=HUMAN, an="Dan")
    rule = build({**ALLOW, "cutover_ref": cutover, "head_ref": "HEAD"}, repo_root=repo)
    assert rule.run() == 0  # the pre-cutover rogue commit is out of range


def test_webflow_merge_committer_passes(tmp_path: Path) -> None:
    """A squash/merge stamped by GitHub's web-flow committer passes: the author is
    the allowlisted agent, the committer is ``GitHub <noreply@github.com>`` (SGO-198)."""
    repo = _init(tmp_path)
    base = _commit(repo, "base", ae=BOT, an="three-cubes-agent[bot]")
    _commit(repo, "merged-work", an="three-cubes-agent[bot]", ae=BOT, cn=WEBFLOW_NAME, ce=WEBFLOW_EMAIL)
    rule = build({**ALLOW, "base_ref": base, "head_ref": "HEAD"}, repo_root=repo)
    assert rule.run() == 0


def test_platform_bot_committers_pass(tmp_path: Path) -> None:
    """Dependabot and Renovate are intrinsically allowed committers (SGO-198)."""
    repo = _init(tmp_path)
    base = _commit(repo, "base", ae=HUMAN, an="Dan")
    _commit(repo, "deps-bump", an="Dan", ae=HUMAN, cn=DEPENDABOT_NAME, ce=DEPENDABOT_EMAIL)
    _commit(repo, "renovate-bump", an="Dan", ae=HUMAN, cn=RENOVATE_NAME, ce=RENOVATE_EMAIL)
    rule = build({**ALLOW, "base_ref": base, "head_ref": "HEAD"}, repo_root=repo)
    assert rule.run() == 0


def test_platform_committer_exemption_bypasses_name_patterns(tmp_path: Path) -> None:
    """The ``[bot]`` in a platform committer name is exempt even under a strict name
    pattern that would otherwise reject the brackets (author name stays pattern-gated)."""
    repo = _init(tmp_path)
    base = _commit(repo, "base", ae=HUMAN, an="Dan")
    # Author "Dan" satisfies the name pattern; committer dependabot[bot]'s brackets do
    # not, but the platform-committer exemption bypasses the name pattern for it.
    _commit(repo, "deps-bump", an="Dan", ae=HUMAN, cn=DEPENDABOT_NAME, ce=DEPENDABOT_EMAIL)
    rule = build(
        {
            "allowed_emails": [BOT, HUMAN],
            "allowed_name_patterns": [r"^[\w .,'-]+$"],
            "base_ref": base,
            "head_ref": "HEAD",
        },
        repo_root=repo,
    )
    assert rule.run() == 0


def test_rogue_author_fails_even_with_webflow_committer(tmp_path: Path) -> None:
    """The committer exemption never rescues a rogue AUTHOR — author stays strict."""
    repo = _init(tmp_path)
    base = _commit(repo, "base", ae=HUMAN, an="Dan")
    _commit(repo, "rogue", an=ROGUE_AUTHOR, ae=ROGUE_EMAIL, cn=WEBFLOW_NAME, ce=WEBFLOW_EMAIL)
    rule = build({**ALLOW, "base_ref": base, "head_ref": "HEAD"}, repo_root=repo)
    assert rule.run() == 1


def test_non_platform_bot_committer_still_fails(tmp_path: Path) -> None:
    """The committer exemption is narrow: a bot committer NOT in the platform set
    (here github-actions[bot]) and off the allowlist is still a violation."""
    repo = _init(tmp_path)
    base = _commit(repo, "base", ae=HUMAN, an="Dan")
    _commit(repo, "work", an="Dan", ae=HUMAN, cn=GHA_NAME, ce=GHA_EMAIL)
    rule = build({**ALLOW, "base_ref": base, "head_ref": "HEAD"}, repo_root=repo)
    assert rule.run() == 1


def test_main_repo_root_and_establish(tmp_path: Path) -> None:
    repo = _init(tmp_path)
    base = _commit(repo, "base", ae=HUMAN)
    _commit(repo, "rogue", an="x", ae="noreply@anthropic.com")
    # Config isn't passed through main() here, so with no allowlist it's a no-op pass.
    assert main(["--repo-root", str(repo)]) == 0
    # Direct rule with allowlist fails, then baseline grandfathers.
    rule = CanonicalCommitIdentity.from_config(
        {**ALLOW, "base_ref": base, "head_ref": "HEAD"}, repo_root=repo
    )
    assert rule.run() == 1
    rule.establish_baseline()
    assert rule.run() == 0
