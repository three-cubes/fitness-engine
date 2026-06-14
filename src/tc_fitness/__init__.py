"""three-cubes-fitness — shared architecture-fitness primitives.

The merged core consumed by Three Cubes repos (kairix, tc-agent-zone). It carries
the baseline-gating helpers and agent-actionable emit/YAML helpers (:mod:`lib`)
plus the unified ratchet grammar (:mod:`ratchet`) that reconciles the three drift
zones between the two repos' independently-grown ratchet gates.

Pin to a git tag when consuming::

    pip install "three-cubes-fitness @ git+https://github.com/three-cubes/fitness-engine.git@v0.2.0"

v0.2.0 is an additive, backward-compatible superset of v0.1.0: every v0.1.0
signature and behaviour is unchanged with defaults. The additions cover
tc-agent-zone's check surface — the 3-marker ``run:`` form on
:func:`~tc_fitness.lib.actionable`, the multiline :func:`~tc_fitness.lib.remediation`
block, the string-keyed :func:`~tc_fitness.lib.gate_keys`, and the ``min_len``
floor override on :func:`~tc_fitness.ratchet.is_vague_reason` /
:func:`~tc_fitness.ratchet.parse_overrides`. kairix may keep its ``@v0.1.0`` pin
unchanged.
"""

from __future__ import annotations

from tc_fitness.lib import (
    REPO_ROOT,
    actionable,
    emit_failures,
    emit_pass,
    gate,
    gate_keys,
    load_yaml,
    main_entry,
    missing_keys,
    python_files,
    remediation,
    repo_relative,
)
from tc_fitness.ratchet import (
    BARE_SUPPRESSION_PATTERNS,
    COVERAGE_OVERRIDE_RE,
    MUTATION_OVERRIDE_RE,
    OVERRIDE_MIN_REASON_LEN,
    SUPPRESSION_PATTERNS,
    VAGUE_OVERRIDE_RE,
    Override,
    contains_suppression,
    is_bare_suppression,
    is_vague_reason,
    make_override_re,
    parse_overrides,
)

__version__ = "0.2.0"

__all__ = [
    "__version__",
    # lib
    "REPO_ROOT",
    "gate",
    "gate_keys",
    "repo_relative",
    "python_files",
    "main_entry",
    "actionable",
    "remediation",
    "emit_failures",
    "emit_pass",
    "load_yaml",
    "missing_keys",
    # ratchet
    "OVERRIDE_MIN_REASON_LEN",
    "VAGUE_OVERRIDE_RE",
    "Override",
    "make_override_re",
    "COVERAGE_OVERRIDE_RE",
    "MUTATION_OVERRIDE_RE",
    "is_vague_reason",
    "parse_overrides",
    "SUPPRESSION_PATTERNS",
    "BARE_SUPPRESSION_PATTERNS",
    "contains_suppression",
    "is_bare_suppression",
]
