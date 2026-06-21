"""three-cubes-fitness — shared architecture-fitness primitives.

The merged core consumed by Three Cubes repos (kairix, tc-agent-zone). It carries
the baseline-gating helpers and agent-actionable emit/YAML helpers (:mod:`lib`),
the unified ratchet grammar (:mod:`ratchet`), and — from v0.3.0 — the
catalogue-driven, repo-agnostic RUNNER (:mod:`runner`, :mod:`context`,
:mod:`staged`, :mod:`catalogue`) that both repos point their ``run_checks.py`` at.

Pin to a git tag when consuming::

    pip install "three-cubes-fitness @ git+https://github.com/three-cubes/fitness-engine.git@v0.3.0"

v0.3.0 is additive over v0.2.0: every v0.1.0 / v0.2.0 signature and behaviour is
unchanged. The runner is a new, optional surface — a consumer's ``run_checks.py``
collapses to ``from tc_fitness.runner import main_cli; from .catalogue import
RULES; raise SystemExit(main_cli(RULES))``. The lib + ratchet modules are
untouched, so a consumer pinned to ``@v0.1.0`` / ``@v0.2.0`` keeps working.
"""

from __future__ import annotations

from importlib import metadata as _metadata

from tc_fitness.baseline import (
    BASELINE_DIRNAME,
    BASELINE_SUFFIX,
    baseline_dir,
    baseline_path,
    establish_baseline,
    load_baseline,
    parse_baseline_text,
    render_baseline,
)
from tc_fitness.catalogue import (
    PROPOSED_STATUS,
    RuleEntry,
    StagedClass,
    is_dispatchable,
)
from tc_fitness.context import CheckContext
from tc_fitness.core_checks import (
    CORE_CHECKS,
    core_check_consistency,
    discover_core_check_modules,
    run_core_check,
)
from tc_fitness.fitness_rule import FitnessRule
from tc_fitness.gate import (
    GateOutcome,
    StepResult,
    run_gate,
)
from tc_fitness.gate_config import (
    GateConfig,
    GateConfigError,
    StepSpec,
    find_config_file,
    load_config,
    load_core_check_configs,
    parse_config,
    parse_core_check_configs,
)
from tc_fitness.keystone import (
    CatalogueConsistencyReport,
    ShrinkResult,
    added_since_tag,
    baseline_shrink_only,
    catalogue_check_consistency,
    find_net_new_violations,
    load_all_baselines,
    net_new_violations_forbidden,
    reconcile_catalogue,
    resolve_previous_tag,
    staged_added_files,
)
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
from tc_fitness.runner import (
    Colours,
    ConditionalCheck,
    ConditionalResult,
    PavedRoadFooter,
    RunnerConfig,
    SkipLineFn,
    Verdicts,
    core_module_name,
    is_core_check,
    main_cli,
    make_env_path_conditional_check,
    print_aggregate,
    resolve_script,
    run,
    select_all,
    select_gate,
    staged_paths,
)
from tc_fitness.staged import (
    EnumerationNarrower,
    LocationMarker,
    ScopeResolver,
    StagedDecision,
    decide,
    filter_to_staged,
    make_binding_narrower,
    make_module_roots_resolver,
    resolve_staged_scope,
    restrict_python_files,
    staged_abs_set,
    staged_in_scope,
)

# Single-sourced from the installed-package metadata (``pyproject.toml`` is the
# one source of truth). The fallback literal is used only for a bare ``sys.path``
# checkout where the distribution isn't installed; it must be kept equal to the
# ``pyproject.toml`` ``version`` so the two never drift (pinned by
# ``tests/test_version.py``).
try:
    __version__ = _metadata.version("three-cubes-fitness")
except _metadata.PackageNotFoundError:  # pragma: no cover - only when not installed
    __version__ = "0.6.1"

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
    # catalogue (schema)
    "RuleEntry",
    "StagedClass",
    "PROPOSED_STATUS",
    "is_dispatchable",
    # context
    "CheckContext",
    # gate orchestrator (the single runnable gate — `tc-fitness run`)
    "GateConfig",
    "StepSpec",
    "GateConfigError",
    "find_config_file",
    "parse_config",
    "load_config",
    "parse_core_check_configs",
    "load_core_check_configs",
    "GateOutcome",
    "StepResult",
    "run_gate",
    # staged selection
    "ScopeResolver",
    "EnumerationNarrower",
    "LocationMarker",
    "StagedDecision",
    "decide",
    "resolve_staged_scope",
    "staged_in_scope",
    "filter_to_staged",
    "staged_abs_set",
    "restrict_python_files",
    "make_module_roots_resolver",
    "make_binding_narrower",
    # runner
    "Colours",
    "Verdicts",
    "RunnerConfig",
    "PavedRoadFooter",
    "ConditionalCheck",
    "ConditionalResult",
    "SkipLineFn",
    "make_env_path_conditional_check",
    "resolve_script",
    "is_core_check",
    "core_module_name",
    "staged_paths",
    "select_all",
    "select_gate",
    "print_aggregate",
    "run",
    "main_cli",
    # v0.6.0 — per-file baseline I/O
    "BASELINE_SUFFIX",
    "BASELINE_DIRNAME",
    "baseline_dir",
    "baseline_path",
    "parse_baseline_text",
    "load_baseline",
    "render_baseline",
    "establish_baseline",
    # v0.6.0 — FitnessRule ABC + CORE-check convention
    "FitnessRule",
    "run_core_check",
    # v0.6.0 — engine CORE-check registry (catalogue ↔ modules consistency)
    "CORE_CHECKS",
    "discover_core_check_modules",
    "core_check_consistency",
    # v0.6.0 — keystone drift-enders
    "load_all_baselines",
    "find_net_new_violations",
    "net_new_violations_forbidden",
    "staged_added_files",
    "added_since_tag",
    "ShrinkResult",
    "resolve_previous_tag",
    "baseline_shrink_only",
    "CatalogueConsistencyReport",
    "reconcile_catalogue",
    "catalogue_check_consistency",
]
