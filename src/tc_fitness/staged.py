"""Precise per-rule staged selection for the fitness runner.

``run_checks.py --staged`` must give fast feedback that the STAGED CHANGE
introduced no fitness violation. The non-negotiable property is **no false
negative on staged changes**: if staging file(s) introduces a violation of rule
R, staged mode MUST run R. Speed is the goal, but a fast path that silently
MISSES a violation is worse than a slow one. When in doubt, run the rule — the
full ``--all`` gate is the merge bar, so over-running is cheap and under-running
is the only real danger.

This module turns each rule's catalogue metadata into a concrete decision:

* **scope predicate** — the repo-relative path prefixes whose staged change
  could trip the rule. Single-sourced: the explicit ``RuleEntry.staged_scope``
  wins; otherwise it is DERIVED from the rule's own detector via an injected
  :class:`ScopeResolver` (the consumer repo's hook — e.g. kairix reads a check
  module's ``RULE.roots`` / ``FitnessRule.roots``). When no scope resolves, the
  predicate is ``None`` → the rule is treated as always-in-scope (fail-safe).

* **selection class** — from :data:`~tc_fitness.catalogue.StagedClass`:
    - ``file-local`` — run over ``staged ∩ scope`` (and the runner scopes the
      shared file index to the staged files so an in-process check walks ONLY
      them). Skipped when that intersection is empty.
    - ``relational`` — if any staged path is within ``scope``, run over the
      FULL scope (a deletion of the paired artefact, or a new surface file, can
      break a cross-file invariant even when the obvious file isn't staged).
    - ``always-run`` — run unconditionally (net-new-file / catalogue-currency /
      README / path-naming — the trigger is "any change at all").

Repo-agnostic scope derivation
------------------------------
Deriving a scope from a check module is repo-specific: kairix introspects its
``FitnessRule`` ABC and import-boundary shims. To stay agnostic, this module
accepts a :class:`ScopeResolver` callable. The runner threads the consumer's
resolver through ``decide``; when none is supplied, only the explicit
``staged_scope`` is honoured and everything else falls back to "run"
(fail-safe). That keeps the common path sound for any repo while letting kairix
supply its FitnessRule-aware resolver to stay byte-identical.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass
from pathlib import Path

from tc_fitness.catalogue import RuleEntry, StagedClass

# A scope resolver maps a check ``script`` filename to the rule's repo-relative
# scan roots, or ``None`` when it can't be derived (→ fail-safe run). The
# consumer repo supplies this so the shared module never needs to know the
# repo's check-module internals.
ScopeResolver = Callable[[str], "tuple[str, ...] | None"]


def resolve_staged_scope(
    entry: RuleEntry,
    script: str,
    resolver: ScopeResolver | None = None,
) -> tuple[str, ...] | None:
    """The repo-relative path-prefix scope for ``entry`` under ``script``.

    Explicit ``entry.staged_scope`` always wins (single source of truth for
    rules whose scope can't be derived — shell detectors, multi-tree standalone
    checks, relational rules with a BROADER trigger than their scan roots).
    Otherwise the scope is derived from the check's own detector via the
    injected ``resolver``. ``None`` means "no resolvable scope" → the caller
    runs the rule unconditionally (fail-safe).
    """
    if entry.staged_scope is not None:
        return entry.staged_scope
    if resolver is None:
        return None
    return resolver(script)


def _path_under(path: str, prefix: str) -> bool:
    """True if repo-relative ``path`` is the file ``prefix`` or sits under the
    directory ``prefix``. A ``prefix`` ending in a file suffix (``.py`` etc.)
    matches that exact file only."""
    if path == prefix:
        return True
    # Directory prefix: ``kairix`` matches ``kairix/...`` but not ``kairixx``.
    return path.startswith(prefix + "/")


def staged_in_scope(scope: tuple[str, ...] | None, staged: list[str]) -> list[str]:
    """The staged paths that fall within ``scope``.

    ``scope is None`` → every staged path is "in scope" (conservative). A
    concrete scope intersects each staged path against its prefixes.
    """
    if scope is None:
        return list(staged)
    return [p for p in staged if any(_path_under(p, prefix) for prefix in scope)]


@dataclass(frozen=True)
class StagedDecision:
    """The runner's decision for one rule against the staged set.

    Attributes:
        run: whether to dispatch the rule at all.
        reason: a short human-readable why (printed in the transparent staged
            ledger so narrowing is auditable, never silent).
        scope_files: for a ``file-local`` rule that should run, the staged
            files to restrict the shared file index to (so the in-process check
            walks ONLY them). Empty/``None`` for relational / always-run (those
            run over their full natural scope).
    """

    run: bool
    reason: str
    scope_files: tuple[str, ...] | None = None


def decide(
    entry: RuleEntry,
    script: str,
    staged: list[str],
    resolver: ScopeResolver | None = None,
) -> StagedDecision:
    """Decide whether — and over what — to run ``entry`` given ``staged``.

    The three classes:

    * ``always-run`` → always dispatch (full scope).
    * ``relational`` → dispatch over full scope iff any staged path is within
      the rule's scope; else skip.
    * ``file-local`` → dispatch over ``staged ∩ scope`` iff that intersection
      is non-empty (and hand those files back so the runner scopes the file
      index); else skip.

    With no staged paths at all (``staged == []``), every rule runs — the
    pre-commit ``--all-files`` quirk must never silently pass.
    """
    klass: StagedClass = entry.staged_class

    if not staged:
        return StagedDecision(run=True, reason="no staged paths — run everything (fail-safe)")

    if klass == "always-run":
        return StagedDecision(run=True, reason="always-run (trigger is any change)")

    scope = resolve_staged_scope(entry, script, resolver)
    matched = staged_in_scope(scope, staged)

    if klass == "relational":
        if matched:
            where = "unresolved scope" if scope is None else ", ".join(scope)
            return StagedDecision(run=True, reason=f"relational — staged path in scope ({where}); full scope")
        return StagedDecision(run=False, reason="relational — no staged path in scope")

    # file-local
    if scope is None:
        # No resolvable scope → can't narrow soundly; run unconditionally.
        return StagedDecision(run=True, reason="file-local — scope unresolved; run (fail-safe)")
    if matched:
        return StagedDecision(
            run=True,
            reason=f"file-local — {len(matched)} staged file(s) in scope",
            scope_files=tuple(matched),
        )
    return StagedDecision(run=False, reason="file-local — no staged file in scope")


# ── file-index narrowing for a file-local rule ──────────────────────────
#
# When a file-local rule runs in staged mode, it only needs to RE-CHECK the
# staged files — every other in-scope file was clean at the previous commit and
# its content is unchanged, so its baseline-diff verdict is unchanged. Narrowing
# the rule's file enumeration to the staged set turns a full-tree walk into a
# handful of files. Soundness note: this only narrows FILE-LOCAL rules, where a
# per-file verdict is independent of the other files. Relational and always-run
# rules are NEVER narrowed.
#
# WHICH enumeration surfaces to narrow is repo-specific (kairix patches its
# ``FitnessRule.enumerate_files`` ABC method plus the ``tc_fitness.python_files``
# free function plus each check module's bound copy). To stay agnostic, the
# runner is handed an ``EnumerationNarrower`` — a context-manager factory the
# consumer supplies. The common runner narrows the package-level
# ``tc_fitness.python_files`` itself; the consumer's narrower layers any
# repo-specific surfaces (its ABC method, its per-check bindings) on top.

# An enumeration narrower takes (repo_root, staged-paths) and returns a context
# manager that, for its duration, restricts every relevant file-enumeration
# surface to the staged files.
EnumerationNarrower = Callable[[Path, list[str]], "AbstractContextManager[None]"]


def filter_to_staged(paths: list[Path], staged_abs: frozenset[Path]) -> list[Path]:
    """Keep only the ``paths`` that are in the staged set (by resolved path).

    A reusable helper for a consumer's own :data:`EnumerationNarrower`: the
    set a narrowed enumeration should yield is exactly ``what-it-would-walk ∩
    staged``.
    """
    out: list[Path] = []
    for p in paths:
        try:
            resolved = p.resolve()
        except OSError:  # pragma: no cover - resolve hiccup → drop conservatively only if not staged
            resolved = p
        if resolved in staged_abs:
            out.append(p)
    return out


def staged_abs_set(repo_root: Path, staged: list[str]) -> frozenset[Path]:
    """The staged repo-relative paths resolved to absolute paths under
    ``repo_root`` — the membership set :func:`filter_to_staged` keys on."""
    return frozenset((repo_root / s).resolve() for s in staged)


@contextmanager
def restrict_python_files(repo_root: Path, staged: list[str]) -> Iterator[None]:
    """Narrow the package-level :func:`tc_fitness.python_files` to ``staged``.

    The repo-agnostic half of the enumeration narrowing: any check that
    enumerates through ``tc_fitness.python_files`` (directly or via
    :func:`tc_fitness.main_entry`) yields only the staged files for the
    duration of the ``with`` block. A consumer with additional enumeration
    surfaces (a ``FitnessRule.enumerate_files`` ABC, per-check ``from
    tc_fitness import python_files`` bindings) supplies its own
    :data:`EnumerationNarrower` that layers those on top of this one.
    """
    import tc_fitness

    staged_abs = staged_abs_set(repo_root, staged)
    real_python_files = tc_fitness.python_files

    def _scoped_python_files(*roots: str, repo_root: Path | None = None, **kwargs: object) -> list[Path]:
        full = real_python_files(*roots, repo_root=repo_root, **kwargs)
        return filter_to_staged(full, staged_abs)

    tc_fitness.python_files = _scoped_python_files
    try:
        yield
    finally:
        tc_fitness.python_files = real_python_files


__all__ = [
    "ScopeResolver",
    "EnumerationNarrower",
    "StagedDecision",
    "decide",
    "resolve_staged_scope",
    "staged_in_scope",
    "filter_to_staged",
    "staged_abs_set",
    "restrict_python_files",
]
