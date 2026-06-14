"""Repo-agnostic catalogue schema — the :class:`RuleEntry` dataclass.

The catalogue is the single source of truth a consumer repo declares its
fitness rules in: one :class:`RuleEntry` row per rule, each pointing at a
check (a python module exposing ``main() -> int``, or a shell script). The
catalogue-driven runner (:mod:`tc_fitness.runner`) reads that list and DERIVES
the dispatch set, so a consumer's ``run-all.sh`` / pre-commit / CI all consume
one declaration instead of editing five files in lockstep.

This module ships the *schema only* — the repo-agnostic fields every Three
Cubes repo's catalogue rows share. It does NOT ship any particular repo's
rows: kairix keeps its ``F26 / F44 / …`` list, tc-agent-zone keeps its own,
and each imports :class:`RuleEntry` from here.

Repo-agnostic id
----------------
``RuleEntry.id`` is a free-form string. It accepts kairix's F-number style
(``"F26"``) AND tc-agent-zone's descriptive style (``"no-duplicate-string"``)
equally — the runner never parses or pattern-matches on it, it is only an
opaque label for the verdict ledger and the ``--gate <id>`` selector.

The two metadata dimensions (``category`` / ``scope``) are open ``str`` here,
not closed ``Literal`` sets: each repo curates its OWN closed vocabulary and
validates membership in its own catalogue test. Pinning the literals in the
shared schema would force every repo onto one taxonomy — the opposite of
repo-agnostic.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

# ── status vocabulary (shared; the runner only special-cases "proposed") ──
#
# The runner skips a ``proposed`` entry (no check exists yet). Every other
# status value is opaque to the runner — a repo may add its own (``vacuous``,
# ``proxy``, ``superseded``, …) and the runner treats them all as
# dispatchable. ``Status`` is therefore an open ``str`` field with a documented
# sentinel rather than a closed literal.
PROPOSED_STATUS = "proposed"
"""The one status value the runner special-cases: a ``proposed`` entry has no
check yet and is excluded from every dispatch mode."""

# ── staged-selection class (the sound per-rule narrowing) ─────────────────
#
# How ``--staged`` decides whether — and over WHAT — to run a rule given the
# staged file set. Three classes, ordered by how much the runner may safely
# narrow. See :mod:`tc_fitness.staged` for the decision logic and the
# soundness contract (no false negative on a staged change).
#
# * ``"file-local"`` — a violation is determinable from a single file in
#   isolation (import-boundary / location / marker / regex rules). A staged
#   change can only NEWLY violate the rule if a staged file is in the rule's
#   path-scope, AND only the staged files need re-checking. → run over
#   ``staged ∩ scope``; skip when that intersection is empty. The default.
#
# * ``"relational"`` — a violation depends on cross-file state (a code surface
#   in tree A paired with a test / spec / route artefact in tree B). A staged
#   change anywhere in the rule's broader scope — INCLUDING a deletion of the
#   paired artefact — can break the invariant. → if any staged path is within
#   the rule's scope, run the rule over its FULL scope.
#
# * ``"always-run"`` — the trigger is "any change at all" (net-new-file
#   detection, catalogue currency, README / path-naming invariants). → always
#   run.
StagedClass = Literal[
    "file-local",
    "relational",
    "always-run",
]


@dataclass(frozen=True)
class RuleEntry:
    """One row in a consumer repo's fitness-rule catalogue.

    The repo-agnostic schema. A repo declares a ``tuple[RuleEntry, ...]`` and
    hands it to :func:`tc_fitness.runner.main_cli`; the runner derives every
    dispatch from these fields.

    Required identity
    -----------------
    * ``id`` — the human-facing label (``"F26"`` OR ``"no-duplicate-string"``).
      Opaque to the runner; used in the ledger and as the ``--gate`` selector.
    * ``gate`` — the baseline-filename root (``"f26"`` →
      ``.architecture/baseline/f26-files.txt``), passed to
      :func:`tc_fitness.gate` / :func:`~tc_fitness.gate_keys`.
    * ``check`` — the python check module name minus the ``check_`` prefix and
      ``.py`` suffix (``"provider_layer_imports"`` →
      ``check_provider_layer_imports.py``). Set to the documented
      :data:`PROPOSED_STATUS` sentinel (``"(proposed)"``) for an unimplemented
      rule.

    Metadata (free-form per repo)
    -----------------------------
    * ``category`` / ``scope`` — open ``str`` taxonomy each repo curates and
      validates in its own catalogue test. ``summary`` — one-line description
      shown in the ledger.

    Dispatch resolution (the catalogue-driven runner)
    -------------------------------------------------
    * ``script`` — the exact script under the checks dir to run, WHEN it
      diverges from the default ``check_<check>.py``. A ``.sh`` value runs as a
      guarded subprocess (a real shell detector whose verdict is produced by
      bash); a ``.py`` value or ``None`` runs the python check IN-PROCESS,
      sharing one :class:`~tc_fitness.context.CheckContext`.

    * ``run_all`` — whether ``--all`` dispatches this entry. Defaults to
      ``True``. Set ``False`` for rules that run elsewhere in the SDLC
      (release-time, security stage, out-of-band) so the runner reproduces
      exactly the set the consumer's ``run-all.sh`` ran.

    * ``subprocess_arg_env`` — for the rare check that takes a runtime argument
      read from an env var (kairix's coverage check reads the Cobertura XML
      path from ``KAIRIX_COVERAGE_XML``). When set, the runner reads that env
      var (falling back to ``subprocess_arg_default`` resolved under the repo
      root); if the resolved path does not exist the rule is SKIPPED
      (``None``-verdict), mirroring a conditional coverage stage.

    Staged selection (precise per-rule narrowing)
    --------------------------------------------
    * ``staged_class`` — one of :data:`StagedClass`. Defaults to
      ``"file-local"`` (most rules are). Set ``"relational"`` for cross-file /
      deletion-sensitive rules and ``"always-run"`` for any-change rules.

    * ``staged_scope`` — the repo-relative path prefixes whose staged change
      could trigger this rule. ``None`` (the default) means "derive it" — the
      runner asks the rule's own detector for its scan roots via the consumer's
      :class:`~tc_fitness.staged.ScopeResolver` hook, and falls back to running
      the rule when no scope resolves (fail-safe, never a silent skip). Set an
      explicit tuple when the relational scope is BROADER than the file-local
      scan roots.

    Paved-road affordance (optional)
    --------------------------------
    * ``exemplar`` — repo-relative path to a canonical PASSING file an agent
      can copy. When a rule carrying an ``exemplar`` FAILS, the runner prints a
      paved-road footer (the consumer supplies the footer text via the runner's
      ``paved_road_footer`` hook).
    * ``task_type`` — zero-or-more free-form task tags (the consumer's own
      query surface uses these; the runner ignores them).
    """

    id: str
    gate: str
    check: str
    category: str = ""
    scope: str = ""
    summary: str = ""
    adr_origin: str | None = None
    status: str = "shipped"
    tags: tuple[str, ...] = field(default_factory=tuple)
    script: str | None = None
    run_all: bool = True
    exemplar: str | None = None
    task_type: tuple[str, ...] = field(default_factory=tuple)
    staged_class: StagedClass = "file-local"
    staged_scope: tuple[str, ...] | None = None
    # Optional runtime-arg wiring for the rare conditional check (coverage).
    subprocess_arg_env: str | None = None
    subprocess_arg_default: str | None = None


def is_dispatchable(entry: RuleEntry) -> bool:
    """True iff ``entry`` has a real check to run.

    A ``proposed`` entry (``status == "proposed"`` OR a placeholder
    ``check`` of ``"(proposed)"``) has no script yet and is excluded from
    every dispatch mode.
    """
    return entry.status != PROPOSED_STATUS and entry.check != "(proposed)"


__all__ = [
    "RuleEntry",
    "StagedClass",
    "PROPOSED_STATUS",
    "is_dispatchable",
]
