"""CORE check modules — the canonical fitness checks every repo INHERITS.

The v0.6.0 promotion (interrogation wss0rcfdr): ~45 checks that every Three
Cubes repo would otherwise reimplement become ENGINE CORE, so a consumer
INHERITS them via its catalogue instead of porting Python. Each CORE check is
a single module under this package exposing:

* a :class:`tc_fitness.fitness_rule.FitnessRule` subclass (the detector), and
* a ``main(argv=None) -> int`` entry point that runs the rule, supporting the
  ``--establish-baseline`` adoption mode.

The CORE-check-module convention
================================

**Location.** One module per check at
``src/tc_fitness/core_checks/<canonical-name>.py`` (canonical name in
``snake_case``; the rule's ``name`` attribute uses the same name in
``kebab-case`` for the baseline file). Tests live at
``tests/core_checks/test_<canonical-name>.py``.

**Module shape.** Copy the exemplar (:mod:`tc_fitness.core_checks.no_duplicate_string`):

.. code-block:: python

    class MyRule(FitnessRule):
        name = "my-rule"                 # → .architecture/baseline/my-rule-files.txt
        remediation = REMEDIATION        # built with tc_fitness.remediation(...)
        extensions = (".py",)            # repo-NEUTRAL default; roots come from config

        def file_has_violation(self, path: Path) -> bool:
            ...

    def build(config, repo_root=None) -> MyRule:
        return MyRule.from_config(config, repo_root=repo_root)

    def main(argv=None) -> int:
        return run_core_check(MyRule, argv)

**Repo-agnostic.** A CORE module contains ZERO repo strings — no ``kairix`` /
``taz`` / ``kata`` paths, globs, or thresholds. Everything repo-specific
(``roots``, ``extensions``, ``exempt_files``, thresholds, the baseline path)
arrives through the consumer's ``[tool.tc_fitness]`` catalogue entry and is
applied via :meth:`FitnessRule.from_config`.

**The catalogue-entry shape a consumer writes.** To bind a CORE check, a
consumer adds a row to its catalogue (``tuple[RuleEntry, ...]``) AND a config
block keyed by the check name. The ``RuleEntry`` points at the CORE module via
its ``check`` field using the ``core:`` namespace:

.. code-block:: python

    # in the consumer's catalogue.py
    RuleEntry(
        id="no-duplicate-string",
        gate="no-duplicate-string",          # baseline filename root
        check="core:no_duplicate_string",    # resolves to tc_fitness.core_checks.no_duplicate_string
        category="maintainability",
        summary="No string literal duplicated 3+ times in one module.",
    )

.. code-block:: toml

    # in the consumer's pyproject.toml [tool.tc_fitness]
    [tool.tc_fitness.core_checks.no_duplicate_string]
    roots = ["scripts", "tools", "src"]
    extensions = [".py"]
    exempt_files = []
    min_length = 10        # rule-specific knob the subclass reads from config
    min_occurrences = 3

The engine resolves the ``core:<module>`` check to
``tc_fitness.core_checks.<module>``, calls its ``build(config, repo_root=...)``
with the matching config block, and runs the returned rule. A consumer pinned
to ``@v0.5.0`` that never adds a ``core:`` row is unaffected (purely additive).

**The shared entry-point helper.** :func:`run_core_check` gives every CORE
module an identical ``main()`` that parses ``--establish-baseline`` and the
optional ``--repo-root``, so no module re-implements argv handling.
"""

from __future__ import annotations

import argparse
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from tc_fitness.fitness_rule import FitnessRule


def run_core_check(
    rule_cls: type[FitnessRule],
    argv: list[str] | None = None,
    *,
    config: Mapping[str, Any] | None = None,
) -> int:
    """Shared ``main()`` body for a CORE check module.

    Parses the two universal flags and dispatches:

    * ``--establish-baseline`` — write today's offenders as the frozen baseline
      (adoption mode) and exit ``0``.
    * ``--repo-root PATH`` — gate a tree other than the CWD (tests / monorepo
      sub-trees).

    ``config`` is the consumer's config block for this check (from
    ``[tool.tc_fitness]``); when ``None`` the rule's class-attribute defaults
    apply. Returns the rule's exit code (or ``0`` after establishing).
    """
    parser = argparse.ArgumentParser(prog=rule_cls.name)
    parser.add_argument(
        "--establish-baseline",
        action="store_true",
        help="freeze today's offenders as the baseline (rule adoption mode).",
    )
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=None,
        help="repo root to scan (default: current working directory).",
    )
    args = parser.parse_args(argv)

    cfg: Mapping[str, Any] = config if config is not None else {}
    rule = rule_cls.from_config(cfg, repo_root=args.repo_root)

    if args.establish_baseline:
        path = rule.establish_baseline()
        print(f"established baseline: {path}")
        return 0
    return rule.run()


# ===========================================================================
# The engine CORE-check registry — the single catalogue of shippable checks
# ===========================================================================
#
# ``CORE_CHECKS`` is the canonical, hand-maintained list of every CORE check
# the engine ships, named in the consumer-facing ``core:<module>`` namespace.
# A consumer binds a subset of these from its own catalogue; the engine
# guarantees (via :func:`core_check_consistency`, exercised in the engine test
# suite) that this registry and the modules ON DISK agree BIDIRECTIONALLY —
# no registry id without a module, no module without a registry id. That is the
# engine-side expression of the F92 ``catalogue_check_consistency`` keystone:
# the catalogue can never silently drift from the checks it claims to ship.
#
# Adding a CORE check is a two-line edit: drop the module under this package and
# add its ``core:<module>`` id here. The consistency test fails otherwise,
# pointing at the orphan module or the dangling id.
CORE_CHECKS: tuple[str, ...] = (
    "core:actionable_feedback",
    "core:adr_number_unique",
    "core:ci_fanin_parity",
    "core:ci_silencers_have_rationale",
    "core:cognitive_complexity",
    "core:coverage_floor",
    "core:coverage_includes_branches",
    "core:empty_body_intent",
    "core:every_test_has_tier_marker",
    "core:integrity_state_predicate",
    "core:license_present",
    "core:mutation_survival_ratchet",
    "core:no_commented_out_code",
    "core:no_duplicate_string",
    "core:no_env_monkeypatch",
    "core:no_hardcoded_repo_paths",
    "core:no_internal_monkeypatch",
    "core:no_internal_patches",
    "core:no_internal_patches_ts",
    "core:no_language_suffix_in_package_names",
    "core:no_llm_attribution",
    "core:no_logging_secrets",
    "core:no_noop_test_scripts",
    "core:no_production_suppressions",
    "core:no_real_names",
    "core:no_test_imports_in_prod",
    "core:no_test_only_kwargs",
    "core:path_naming",
    "core:pattern_chokepoint",
    "core:posix_path_serialisation",
    "core:readme_resolver_coverage",
    "core:schema_conformance",
    "core:script_help_smoke",
    "core:shellcheck_disable_with_reason",
    "core:sonar_ignore_rationale",
    "core:suppressions_have_rationale",
    "core:test_skip_rationale",
    "core:unused_params_named",
)

_CORE_NAMESPACE = "core:"


def discover_core_check_modules() -> tuple[str, ...]:
    """Enumerate the CORE-check modules present on disk, ``core:``-namespaced.

    Walks this package directory for ``<module>.py`` files (excluding dunder
    modules) and returns each as a ``core:<module>`` id — the same namespace
    :data:`CORE_CHECKS` declares. The discovery source of truth for the
    bidirectional :func:`core_check_consistency` reconciliation.
    """
    here = Path(__file__).parent
    out: list[str] = []
    for path in sorted(here.glob("*.py")):
        stem = path.stem
        if stem.startswith("_"):
            continue
        out.append(f"{_CORE_NAMESPACE}{stem}")
    return tuple(out)


def core_check_consistency() -> int:
    """Gate: :data:`CORE_CHECKS` ↔ on-disk modules agree bidirectionally.

    Reuses the engine keystone :func:`tc_fitness.catalogue_check_consistency`
    over the declared registry and the discovered modules, so a CORE module
    added without a registry id (orphan) OR a registry id with no module
    (dangling) FAILS. Returns ``0`` when consistent, ``1`` on drift.
    """
    from tc_fitness.keystone import catalogue_check_consistency
    from tc_fitness.lib import remediation as _remediation

    return catalogue_check_consistency(
        cataloged_check_ids=CORE_CHECKS,
        available_check_ids=discover_core_check_modules(),
        remediation=_remediation(
            fix=(
                "reconcile CORE_CHECKS (in tc_fitness.core_checks) with the modules "
                "on disk: add the missing core:<module> id for a new check module, "
                "or remove the dangling id for a module that no longer exists."
            ),
            nxt="re-run the engine test suite to confirm the registry is consistent.",
            run="python -c 'from tc_fitness.core_checks import core_check_consistency as c; raise SystemExit(c())'",
        ),
    )


__all__ = [
    "run_core_check",
    "CORE_CHECKS",
    "discover_core_check_modules",
    "core_check_consistency",
]
