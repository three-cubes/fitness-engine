"""CORE check: coverage_includes_branches — the report measures branches.

Pure line coverage proves "this line executed" but not "both sides of this
conditional were taken". A suite can show 100% line coverage and still miss
half the logic. This rule asserts the coverage report carries non-zero branch
coverage, so the floor it feeds (see :mod:`coverage_floor`) is measuring
branches, not just lines.

Shape note. This is a single-artifact assertion, not a per-file ratchet, so it
overrides :meth:`enumerate_files` to yield the one coverage report and
:meth:`is_in_scope` to admit it. The baseline machinery still applies (the
report path can be grandfathered), but in practice the report is either
branch-aware or it is not — there is nothing to grandfather, so the baseline
stays empty and the gate FAILS the moment a real report reports zero branches.

Ported from tc-agent-zone ``scripts/checks/coverage_includes_branches.py``
(FEAT-150 G4) — re-expressed as a configurable, repo-agnostic rule. The report
path is CONFIG; nothing here names a repo.
"""

from __future__ import annotations

import importlib
from collections.abc import Callable, Mapping
from pathlib import Path
from types import ModuleType
from typing import Any

from tc_fitness.core_checks import run_core_check
from tc_fitness.fitness_rule import FitnessRule
from tc_fitness.lib import remediation as _remediation

#: Default coverage report location relative to the repo root. Overridable.
DEFAULT_COVERAGE_REPORT = "coverage.xml"

REMEDIATION = _remediation(
    fix=(
        "enable branch coverage in the coverage config (for a Cobertura report "
        "from coverage.py, set branch = true under [tool.coverage.run], or pass "
        "--cov-branch). If the report is already branch-aware but reports zero "
        "branches, the suite exercises no conditionals — add tests that take "
        "both sides of a branch."
    ),
    nxt="re-run this check after the next coverage run emits the report.",
    run="python -m tc_fitness.core_checks.coverage_includes_branches",
    passing="branch = true so the report carries branch-rate > 0 and branches-valid > 0",
    forbidden="ship a report whose root reports branch-rate of 0 after a real run",
)


def _reject_unsafe_xml(text: str, source: str) -> None:
    """Reject DTD/entity declarations before any parser runs (XXE guard)."""
    lowered = text.lower()
    if "<!doctype" in lowered or "<!entity" in lowered:
        raise ValueError(f"unsafe coverage XML at {source}: DTD/entity declarations are not allowed")


def _resolve_element_tree(
    import_module: Callable[[str], ModuleType] = importlib.import_module,
) -> Any:
    """Prefer defusedxml; fall back to stdlib after explicit DTD/entity rejection."""
    try:
        from defusedxml import ElementTree as DefusedET

        return DefusedET
    except ImportError:
        return import_module("xml.etree.ElementTree")


def report_lacks_branches(report_path: Path, *, element_tree: Any | None = None) -> bool:
    """True iff the coverage report exists but records no branch coverage.

    Reads the root ``<coverage>`` element's ``branch-rate`` and
    ``branches-valid`` attributes: a real branch-aware report carries both > 0.
    A MISSING report returns ``False`` (nothing to assert yet — another run
    will produce it). A malformed/unsafe report raises, surfacing the problem
    rather than silently passing.
    """
    if not report_path.exists():
        return False
    text = report_path.read_text(encoding="utf-8")
    _reject_unsafe_xml(text, str(report_path))
    et = element_tree if element_tree is not None else _resolve_element_tree()
    root = et.parse(report_path).getroot()
    branch_rate = float(root.attrib.get("branch-rate", "0") or "0")
    branches_valid = int(root.attrib.get("branches-valid", "0") or "0")
    return branch_rate <= 0.0 or branches_valid <= 0


class CoverageIncludesBranches(FitnessRule):
    """Flags a coverage report that measures lines only, not branches."""

    name = "coverage-includes-branches"
    remediation = REMEDIATION
    extensions = (".xml",)

    #: Rule-specific knob — instance attr so ``from_config`` overrides it.
    coverage_report: str = DEFAULT_COVERAGE_REPORT

    @classmethod
    def from_config(
        cls,
        config: Mapping[str, Any],
        *,
        repo_root: Path | None = None,
    ) -> CoverageIncludesBranches:
        """Build from config, also reading ``coverage_report``."""
        rule = super().from_config(config, repo_root=repo_root)
        assert isinstance(rule, CoverageIncludesBranches)  # noqa: S101  # narrowing for mypy
        rule.coverage_report = str(config.get("coverage_report", DEFAULT_COVERAGE_REPORT))
        return rule

    def _report_path(self) -> Path:
        report = Path(self.coverage_report)
        return report if report.is_absolute() else self._repo_root / report

    def enumerate_files(self) -> list[Path]:
        """The single artifact this rule judges: the coverage report itself."""
        report = self._report_path()
        return [report] if report.exists() else []

    def is_in_scope(self, rel: str) -> bool:
        """Admit the configured report regardless of where it sits."""
        return True

    def file_has_violation(self, path: Path) -> bool:
        return report_lacks_branches(path)


def build(
    config: Mapping[str, Any],
    *,
    repo_root: Path | None = None,
) -> CoverageIncludesBranches:
    """Factory the engine calls to bind this CORE check to a consumer's config."""
    return CoverageIncludesBranches.from_config(config, repo_root=repo_root)


def main(argv: list[str] | None = None) -> int:
    """CLI entry — supports ``--establish-baseline`` and ``--repo-root``."""
    return run_core_check(CoverageIncludesBranches, argv)


if __name__ == "__main__":
    import sys

    sys.exit(main())
