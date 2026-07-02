"""CORE check: canonical_commit_identity — commits are authored by allowed identities.

Machine-enforced identity hygiene (Autonomous Delivery Platform SP-A / SGO-158):
every commit in the PR range must carry an author AND committer whose email is
on the consumer's allowlist (the canonical agent GitHub App + named human
maintainers), and — when name patterns are configured — a name matching one of
them (catching emoji/marker-in-name identities like ``Builder 🔨``).

The AUTHOR is held strictly to that allowlist — author identity is what governance
cares about. The COMMITTER is checked against the allowlist UNION an intrinsic set
of platform committers — GitHub's web-flow signer plus Dependabot/Renovate (SGO-198):
GitHub rewrites the committer to ``GitHub <noreply@github.com>`` on every squash/merge
performed through the UI, so gating the committer strictly would fail any scanned
range that includes a merged commit. These platform identities are always allowed as
*committers* so the check can't be broken per-repo by forgetting to allowlist them.

This is a RANGE check, not a file check: the unit of violation is a commit, so
it overrides :meth:`collect_violations` and reads ``git log`` rather than
walking files. It is repo-agnostic — the allowlist, the name patterns, and the
range refs are ALL consumer config; the engine ships no identities.

Guard-forward (decision D2): a ``cutover_ref`` bounds enforcement to
``cutover_ref..HEAD`` so historical commits made before the standard was adopted
never fail. With no allowlist configured the rule is a NO-OP, so a consumer that
hasn't opted in is never broken.
"""

from __future__ import annotations

import re
import subprocess
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from tc_fitness.core_checks import run_core_check
from tc_fitness.fitness_rule import FitnessRule
from tc_fitness.lib import remediation as _remediation

DEFAULT_BASE_REF = "origin/main"
DEFAULT_HEAD_REF = "HEAD"

#: Unit-separator delimited git-log record: sha, author name/email, committer name/email.
_SEP = "\x1f"
_FORMAT = _SEP.join(("%H", "%an", "%ae", "%cn", "%ce"))

#: Committers that platform automation stamps onto otherwise-canonical commits and
#: which are therefore ALWAYS allowed as *committers* (never as authors): GitHub's
#: web-flow signer (``GitHub <noreply@github.com>``), set as the committer on every
#: squash/merge performed through the UI, plus the Dependabot and Renovate bot
#: accounts. The committer on a merged or bot-raised commit is the platform, not a
#: rogue identity, so gating it strictly would fail every consumer the moment it
#: accrues merged history (SGO-198). Web-flow is matched by exact email; the bots by
#: the stable ``+<bot>[bot]@users.noreply.github.com`` no-reply suffix GitHub mints.
_WEBFLOW_COMMITTER_EMAIL = "noreply@github.com"
_PLATFORM_COMMITTER_SUFFIXES: tuple[str, ...] = (
    "+dependabot[bot]@users.noreply.github.com",
    "+renovate[bot]@users.noreply.github.com",
)


def _is_platform_committer(email: str) -> bool:
    """True for the GitHub web-flow / Dependabot / Renovate committer identities."""
    return email == _WEBFLOW_COMMITTER_EMAIL or email.endswith(_PLATFORM_COMMITTER_SUFFIXES)


REMEDIATION = _remediation(
    fix=(
        "re-author the commit(s) under the canonical identity — mint a per-agent App "
        "token (agent-token) so the author/committer is the three-cubes-agent App, or "
        "for local work set git user.name/user.email to an allowlisted identity and "
        "`git commit --amend --reset-author`. Add a genuinely new human maintainer to "
        "the check's `allowed_emails` (a CODEOWNERS-gated control-plane edit)."
    ),
    nxt="re-run this check to confirm it goes green.",
    run="python -m tc_fitness.core_checks.canonical_commit_identity",
    passing="author + committer = three-cubes-agent[bot] or an allowlisted human",
    forbidden="author feat-156-deploy <noreply@anthropic.com>  (off-allowlist identity)",
)


def _log_identities(repo_root: Path, rev_range: str) -> list[tuple[str, str, str, str, str]]:
    """``(sha, author_name, author_email, committer_name, committer_email)`` per commit.

    Returns ``[]`` on any git failure (e.g. an unresolved range in a fresh
    checkout) — an unresolvable range yields no commits to gate, never a crash.
    """
    result = subprocess.run(
        ["git", "log", f"--format={_FORMAT}", rev_range],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        return []
    rows: list[tuple[str, str, str, str, str]] = []
    for line in result.stdout.splitlines():
        if not line.strip():
            continue
        parts = line.split(_SEP)
        if len(parts) == 5:
            rows.append((parts[0], parts[1], parts[2], parts[3], parts[4]))
    return rows


class CanonicalCommitIdentity(FitnessRule):
    """Flags commits whose author/committer identity is off the allowlist."""

    name = "canonical-commit-identity"
    remediation = REMEDIATION

    #: Config (repo-neutral defaults; overridden per consumer via from_config).
    allowed_emails: frozenset[str] = frozenset()
    allowed_name_patterns: tuple[re.Pattern[str], ...] = ()
    base_ref: str = DEFAULT_BASE_REF
    head_ref: str = DEFAULT_HEAD_REF
    cutover_ref: str | None = None

    @classmethod
    def from_config(
        cls,
        config: Mapping[str, Any],
        *,
        repo_root: Path | None = None,
    ) -> CanonicalCommitIdentity:
        rule = super().from_config(config, repo_root=repo_root)
        assert isinstance(rule, CanonicalCommitIdentity)  # noqa: S101  # narrowing for mypy
        rule.allowed_emails = frozenset(config.get("allowed_emails", ()))
        rule.allowed_name_patterns = tuple(re.compile(p) for p in config.get("allowed_name_patterns", ()))
        rule.base_ref = str(config.get("base_ref", DEFAULT_BASE_REF))
        rule.head_ref = str(config.get("head_ref", DEFAULT_HEAD_REF))
        cutover = config.get("cutover_ref")
        rule.cutover_ref = str(cutover) if cutover else None
        return rule

    def _configured(self) -> bool:
        """The rule only bites once a consumer supplies an allowlist / patterns."""
        return bool(self.allowed_emails or self.allowed_name_patterns)

    def _rev_range(self) -> str:
        left = self.cutover_ref if self.cutover_ref else self.base_ref
        return f"{left}..{self.head_ref}"

    def _identity_ok(self, name: str, email: str) -> bool:
        if self.allowed_emails and email not in self.allowed_emails:
            return False
        if self.allowed_name_patterns and not any(p.search(name) for p in self.allowed_name_patterns):
            return False
        return True

    def _committer_ok(self, name: str, email: str) -> bool:
        """Committer identity check: the configured allowlist plus platform committers.

        Unlike the author, the committer of a merged commit is GitHub's web-flow
        signer and the committer of a bot-raised PR is the bot itself; those
        platform identities (:data:`_is_platform_committer`) are always allowed,
        bypassing both the email allowlist and the name patterns.
        """
        return _is_platform_committer(email) or self._identity_ok(name, email)

    def file_has_violation(self, path: Path) -> bool:
        """Unused — this is a range/commit check (see :meth:`collect_violations`)."""
        return False

    def enumerate_files(self) -> list[Path]:
        """No file surface — identity lives in commit metadata, not the tree."""
        return []

    def collect_violations(self) -> set[Path]:
        """Every in-range commit with an off-allowlist author/committer identity.

        The AUTHOR is held strictly to the configured allowlist; the COMMITTER is
        allowed if it is a platform committer (web-flow / Dependabot / Renovate) or
        otherwise on the allowlist — see :meth:`_committer_ok`.
        """
        if not self._configured():
            return set()
        out: set[Path] = set()
        for sha, an, ae, cn, ce in _log_identities(self._repo_root, self._rev_range()):
            bad: list[str] = []
            if not self._identity_ok(an, ae):
                bad.append(f"author {an} <{ae}>")
            if not self._committer_ok(cn, ce):
                bad.append(f"committer {cn} <{ce}>")
            if bad:
                out.add(Path(f"{sha[:12]} {'; '.join(bad)}"))
        return out


def build(
    config: Mapping[str, Any],
    *,
    repo_root: Path | None = None,
) -> CanonicalCommitIdentity:
    """Factory the engine calls to bind this CORE check to a consumer's config."""
    return CanonicalCommitIdentity.from_config(config, repo_root=repo_root)


def main(argv: list[str] | None = None) -> int:
    """CLI entry — supports ``--establish-baseline`` and ``--repo-root``."""
    return run_core_check(CanonicalCommitIdentity, argv)


if __name__ == "__main__":
    import sys

    sys.exit(main())
