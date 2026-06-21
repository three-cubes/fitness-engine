"""CORE check: every-test-has-tier-marker — each test declares its tier.

An interface-driven test architecture needs every test to declare which lane
it belongs to (a fast lane on every commit, a slower lane in CI) so the tier
guarantees stay live. Without this gate, untagged tests drift into the slow
lane (or are never run). This rule flags any test file where a ``test_*``
function carries no tier marker and no module-level ``pytestmark`` pins one.

Detection (AST walk per file):

  1. A module-level ``pytestmark = ...`` pinning one of the configured tier
     marker names covers every test in the file -- pass.
  2. Otherwise every ``test_*`` function must carry a matching
     ``@pytest.mark.<tier>`` decorator.
  3. A file with no ``test_*`` functions (a fixtures/support module) passes.

The gate checks only the *presence* of a tier marker, not which one is right
-- mis-classification is a code-review concern; absence is caught here.

Ported from tc-agent-zone ``scripts/checks/every_test_has_tier_marker.py``
and re-expressed as a configurable, repo-agnostic rule: scan roots and the
excluded path components arrive from config; the tier marker vocabulary is
the rule's own shape (``unit`` / ``contract`` / ``e2e``) and is overridable.
"""

from __future__ import annotations

import ast
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from tc_fitness.core_checks import run_core_check
from tc_fitness.fitness_rule import FitnessRule
from tc_fitness.lib import remediation as _remediation

#: The default tier vocabulary -- the test-architecture's own shape, not repo
#: identity. A consumer with a different taxonomy overrides via config.
DEFAULT_TIER_MARKERS: tuple[str, ...] = ("unit", "contract", "e2e")

#: Path components that mark a support/fixture subtree to skip even when it
#: holds ``test_*.py`` files. Overridable via config.
DEFAULT_EXCLUDED_PARTS: tuple[str, ...] = ("node_modules", ".venv", "__pycache__", "fixtures")

REMEDIATION = _remediation(
    fix=(
        "add pytestmark = pytest.mark.<tier> at module level (when every test "
        "in the file shares one tier) OR decorate each test_* function with "
        "@pytest.mark.<tier>. Pick the tier by the test's reach: a pure "
        "single-unit test, a single-interface contract test, or a "
        "cross-boundary end-to-end test."
    ),
    nxt="re-run this check to confirm it goes green.",
    run="python -m tc_fitness.core_checks.every_test_has_tier_marker",
    passing="pytestmark = pytest.mark.unit  # at module level, covers every test",
    forbidden="def test_parser(): ...  # no tier marker anywhere -- drifts off every lane",
)


def _extract_marker_names(value: ast.expr) -> set[str]:
    """Return ``pytest.mark.<name>`` attributes referenced under ``value``."""
    out: set[str] = set()
    if isinstance(value, ast.List | ast.Tuple):
        for elt in value.elts:
            out |= _extract_marker_names(elt)
        return out
    if isinstance(value, ast.Attribute):
        if (
            isinstance(value.value, ast.Attribute)
            and value.value.attr == "mark"
            and isinstance(value.value.value, ast.Name)
            and value.value.value.id == "pytest"
        ):
            out.add(value.attr)
        return out
    if isinstance(value, ast.Call):
        out |= _extract_marker_names(value.func)
        return out
    return out


def _module_tier_marker(tree: ast.Module, tiers: frozenset[str]) -> set[str]:
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if isinstance(target, ast.Name) and target.id == "pytestmark":
                return _extract_marker_names(node.value) & tiers
    return set()


def _function_tier_marker(node: ast.FunctionDef | ast.AsyncFunctionDef, tiers: frozenset[str]) -> set[str]:
    out: set[str] = set()
    for dec in node.decorator_list:
        out |= _extract_marker_names(dec)
    return out & tiers


def _untagged_functions(tree: ast.Module, tiers: frozenset[str]) -> list[str]:
    out: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        if not node.name.startswith("test_"):
            continue
        if not _function_tier_marker(node, tiers):
            out.append(node.name)
    return out


def file_missing_tier_marker(path: Path, *, tiers: frozenset[str]) -> bool:
    """True iff ``path`` holds a ``test_*`` function with no tier marker.

    Pure helper (the detection core): a module-level ``pytestmark`` tier
    covers the whole file; otherwise every ``test_*`` function must carry one.
    A file with no test functions (a fixtures module) is not a violation. A
    syntax / decode error is treated as "no violation".
    """
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (SyntaxError, UnicodeDecodeError, OSError):
        return False
    if _module_tier_marker(tree, tiers):
        return False
    return bool(_untagged_functions(tree, tiers))


class EveryTestHasTierMarker(FitnessRule):
    """Flags test files whose tests lack a tier marker."""

    name = "every-test-has-tier-marker"
    remediation = REMEDIATION
    extensions = (".py",)

    #: Rule-specific knobs.
    tier_markers: tuple[str, ...] = DEFAULT_TIER_MARKERS
    excluded_parts: tuple[str, ...] = DEFAULT_EXCLUDED_PARTS

    @classmethod
    def from_config(
        cls,
        config: Mapping[str, Any],
        *,
        repo_root: Path | None = None,
    ) -> EveryTestHasTierMarker:
        rule = super().from_config(config, repo_root=repo_root)
        assert isinstance(rule, EveryTestHasTierMarker)  # noqa: S101  # narrowing for mypy
        markers = config.get("tier_markers")
        rule.tier_markers = tuple(markers) if markers is not None else DEFAULT_TIER_MARKERS
        excluded = config.get("excluded_parts")
        rule.excluded_parts = tuple(excluded) if excluded is not None else DEFAULT_EXCLUDED_PARTS
        return rule

    def is_in_scope(self, rel: str) -> bool:
        """Tier markers only apply to ``test_*`` modules; skip support trees."""
        if not super().is_in_scope(rel):
            return False
        parts = Path(rel).parts
        if any(part in self.excluded_parts for part in parts):
            return False
        return Path(rel).name.startswith("test_")

    def file_has_violation(self, path: Path) -> bool:
        return file_missing_tier_marker(path, tiers=frozenset(self.tier_markers))


def build(config: Mapping[str, Any], *, repo_root: Path | None = None) -> EveryTestHasTierMarker:
    """Factory the engine calls to bind this CORE check to a consumer's config."""
    return EveryTestHasTierMarker.from_config(config, repo_root=repo_root)


def main(argv: list[str] | None = None) -> int:
    """CLI entry — supports ``--establish-baseline`` and ``--repo-root``."""
    return run_core_check(EveryTestHasTierMarker, argv)


if __name__ == "__main__":
    import sys

    sys.exit(main())
