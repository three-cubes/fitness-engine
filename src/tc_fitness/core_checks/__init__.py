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


__all__ = ["run_core_check"]
