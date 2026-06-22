"""Branch-naming convention gate — the Linear ``gitBranchName`` shape.

A configurable engine gate lifting tc-agent-zone's
``scripts/checks/branch_naming.py``. The convention is Linear's ``gitBranchName``
shape ``<user>/<team>-<number>-<slug>`` (e.g.
``dan/kno-45-pr-a-sync-dispatch-table``), so a branch carries its Linear issue
identifier and the issue↔branch↔PR link is automatic.

Shared machinery, per-repo domain: the pattern and BOTH exempt sets are config.
The engine ships sensible Linear defaults, but each consumer extends them — taz
keeps ``develop`` in its exempt branches, kairix doesn't; another repo can pass a
completely different ``pattern``. Nothing repo-specific is baked into a default.

A consumer's ``scripts/checks/branch_naming.py`` reduces to a thin shim::

    from tc_fitness.checks.branch_naming import current_branch, check_branch
    raise SystemExit(check_branch(
        current_branch(),
        exempt_branches={"main", "develop", "HEAD"},  # taz keeps develop
    ))
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from collections.abc import Iterable, Mapping
from pathlib import Path

#: The GitHub Actions env var carrying the PR's HEAD ref (the source branch
#: name) — set ONLY on ``pull_request`` / ``pull_request_target`` events. On
#: those events the runner checks out the merge commit in DETACHED HEAD, so
#: ``git rev-parse --abbrev-ref HEAD`` returns the literal ``"HEAD"`` (which is
#: exempt) and the rule would silently no-op. Resolving this var first makes the
#: gate bite the ACTUAL PR branch name. See :func:`current_branch`.
GITHUB_HEAD_REF_ENV = "GITHUB_HEAD_REF"

#: The Linear ``gitBranchName`` shape ``<user>/<team>-<number>-<slug>``:
#:   user   — the assignee's git-branch alias (``dan``)
#:   team   — the Linear team key, lowercased (``kno``, ``pla``)
#:   number — the Linear issue number
#:   slug   — the kebab/underscore issue-title slug
DEFAULT_LINEAR_PATTERN = re.compile(
    r"^[a-z][a-z0-9-]*/"  # <user>/
    r"[a-z][a-z0-9]*-\d+"  # <team>-<number>
    r"-[a-z0-9][a-z0-9_-]*$"  # -<slug>
)

#: Branch names always allowed regardless of the pattern. A consumer extends or
#: replaces this — taz adds ``develop`` (which it keeps), kairix doesn't.
#:
#: ``"HEAD"`` is retained as a belt-and-braces exempt: :func:`current_branch`
#: now maps a detached ``"HEAD"`` to ``None`` (a clean skip) BEFORE it reaches
#: :func:`check_branch`, but a consumer that passes a literal ``"HEAD"`` by hand
#: still gets a clean exempt rather than a confusing convention failure.
DEFAULT_EXEMPT_BRANCHES: frozenset[str] = frozenset({"main", "HEAD"})

#: Automation / tooling branch patterns always allowed.
DEFAULT_EXEMPT_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"^worktree-agent-[a-f0-9]+$"),  # Claude Code sub-agent worktrees
    re.compile(r"^agent/"),  # autonomous-agent PR branches (the three-cubes-agent App)
    re.compile(r"^gh-pages$"),
    re.compile(r"^renovate/"),  # Renovate
    re.compile(r"^dependabot/"),  # Dependabot
)


def current_branch(
    repo_root: Path | None = None,
    *,
    env: Mapping[str, str] | None = None,
) -> str | None:
    """The branch name the gate should check, or ``None`` when undeterminable.

    Resolution order (the detached-HEAD fix):

    1. ``$GITHUB_HEAD_REF`` when set + non-empty — the authoritative PR HEAD ref
       on a GitHub ``pull_request`` event. On that event the runner checks out
       the merge commit in DETACHED HEAD, so ``git rev-parse --abbrev-ref HEAD``
       resolves to the literal ``"HEAD"`` — which is exempt — and the rule would
       silently no-op on *every* PR. Reading the env var first makes the gate
       check the ACTUAL source branch name, so a bad PR branch name fails.
    2. otherwise ``git rev-parse --abbrev-ref HEAD`` — the local / ``push``-event
       branch name.

    ``None`` means "not on a branch" (genuine detached HEAD with no PR ref, or
    not a git repo) — the caller treats it as a clean skip, never a false
    failure. A literal ``"HEAD"`` from ``git`` (detached, no PR ref) is mapped to
    ``None`` for the same reason: it is the absence of a branch, not a name to
    gate."""
    environ = env if env is not None else os.environ
    head_ref = environ.get(GITHUB_HEAD_REF_ENV, "").strip()
    if head_ref:
        return head_ref

    root = repo_root if repo_root is not None else Path.cwd()
    try:
        out = subprocess.check_output(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=root, text=True
        ).strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None
    if not out or out == "HEAD":
        # Detached HEAD with no PR ref → not on a branch → clean skip.
        return None
    return out


def check_branch(
    branch: str | None,
    *,
    exempt_branches: Iterable[str] = DEFAULT_EXEMPT_BRANCHES,
    exempt_patterns: tuple[re.Pattern[str], ...] = DEFAULT_EXEMPT_PATTERNS,
    pattern: re.Pattern[str] = DEFAULT_LINEAR_PATTERN,
) -> int:
    """Gate ``branch`` against the naming convention; print + return exit code.

    Args:
        branch: the branch name to check, or ``None`` (→ clean skip).
        exempt_branches: branch names always allowed (config — taz keeps
            ``develop``, kairix doesn't).
        exempt_patterns: compiled patterns always allowed (automation branches).
        pattern: the compiled convention pattern (default: the Linear shape).

    Returns:
        ``0`` when the branch is exempt or matches ``pattern`` (or is ``None``);
        ``1`` with an actionable remediation on stderr otherwise.
    """
    if branch is None:
        print("SKIP branch_naming: not on a branch (detached HEAD or no git repo)")
        return 0

    exempt = set(exempt_branches)
    if branch in exempt:
        print(f"PASS branch_naming (exempt: {branch})")
        return 0
    for pat in exempt_patterns:
        if pat.match(branch):
            print(f"PASS branch_naming (exempt pattern: {branch})")
            return 0
    if pattern.match(branch):
        print(f"PASS branch_naming ({branch})")
        return 0

    print(f"FAIL branch_naming: branch '{branch}' does not match convention", file=sys.stderr)
    print(
        f"  - branches must follow Linear's gitBranchName shape "
        f"<user>/<team>-<number>-<slug> (e.g. dan/kno-45-sync-dispatch-table); "
        f"fix: copy the branch name from the Linear issue ('Copy git branch name') "
        f"and rename via `git branch -m {branch} <user>/<team>-<number>-<slug>`; "
        f"next: push the new name with `git push -u origin <new-name>`, then delete the "
        f"old with `git push origin --delete {branch}`",
        file=sys.stderr,
    )
    return 1


def main(argv: list[str] | None = None) -> int:
    """Resolve the current branch from git and gate it with the defaults.

    A consumer typically calls :func:`check_branch` directly with its own exempt
    sets; this entrypoint exists for a bare ``python -m`` invocation."""
    return check_branch(current_branch())


if __name__ == "__main__":
    sys.exit(main())
