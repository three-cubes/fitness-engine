"""CORE check: coverage_floor — per-file line-coverage floor.

Repository-wide coverage averages hide files at 0%: a 91% repo mean can
conceal a source file with no tests at all. This rule enforces a PER-FILE
floor — every source file recorded in a coverage report must clear a minimum
line-coverage percentage — so the gap is surfaced file-by-file rather than
washed out in the mean.

The rule is the :class:`tc_fitness.fitness_rule.FitnessRule` expression of a
coverage *ratchet*: today's below-floor files are grandfathered into the
per-file baseline (``--establish-baseline``), and the gate FAILS only when a
file NOT in the baseline drops below the floor. The baseline may only shrink,
so coverage debt is paid down, never accreted.

Ported from kairix ``scripts/checks/check_per_file_coverage.py`` (F7/F9) and
the coverage.xml parsing in tc-agent-zone ``scripts/checks/coverage_ratchet.py``
— re-expressed as a configurable, repo-agnostic rule. The floor, the coverage
report path, and the scan roots are CONFIG the consumer supplies; nothing here
names a repo, a source package, or a hardcoded threshold beyond the
domain-intrinsic default.
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

#: The domain-intrinsic default floor. Per-file line coverage below this is a
#: gap worth surfacing. Overridable per consumer via ``floor_pct``.
DEFAULT_FLOOR_PCT = 90.0

#: Default coverage report location relative to the repo root. Overridable.
DEFAULT_COVERAGE_REPORT = "coverage.xml"

REMEDIATION = _remediation(
    fix=(
        "ask what DEFECT CLASS the coverage gap proxies before padding tests: "
        "a missing failure-mode test at a boundary, a missing scale-bound test, "
        "or a genuinely production-only adapter. Add the test that proves the "
        "behaviour; do not call a function once just to push the percentage up."
    ),
    nxt="re-run this check to confirm the file clears the floor.",
    run="python -m tc_fitness.core_checks.coverage_floor",
    passing="extract testable logic behind a deps seam, then unit-test the branches",
    forbidden="add a no-op test that calls the function once to lift the percentage",
)


def _reject_unsafe_xml(text: str, source: str) -> None:
    """Reject DTD/entity declarations before any parser runs (XXE guard)."""
    lowered = text.lower()
    if "<!doctype" in lowered or "<!entity" in lowered:
        raise ValueError(f"unsafe coverage XML at {source}: DTD/entity declarations are not allowed")


def _resolve_element_tree(
    import_module: Callable[[str], ModuleType] = importlib.import_module,
) -> Any:
    """Prefer defusedxml; fall back to stdlib after explicit DTD/entity rejection.

    ``import_module`` is a DI seam so a test can drive either path without
    monkeypatching the production module.
    """
    try:
        from defusedxml import ElementTree as DefusedET

        return DefusedET
    except ImportError:
        return import_module("xml.etree.ElementTree")


def parse_coverage_report(report_path: Path, *, element_tree: Any | None = None) -> dict[str, float]:
    """Return ``{<source>/<filename>: line-rate-percent}`` from a Cobertura report.

    Cobertura XML declares ``<source>`` roots and emits ``<class filename=...>``
    paths relative to a source. The returned keys join the first source root
    with each class filename so they read as report-relative paths (the same
    shape the scan roots filter against). When multiple classes resolve to one
    key, the LOWEST line-rate wins (the pessimistic reading). A missing report
    yields an empty mapping — the caller decides whether that is in scope.
    """
    if not report_path.exists():
        return {}
    text = report_path.read_text(encoding="utf-8")
    _reject_unsafe_xml(text, str(report_path))
    et = element_tree if element_tree is not None else _resolve_element_tree()
    root = et.parse(report_path).getroot()

    source_roots = [s.text.strip().strip("/") for s in root.iter("source") if s.text and s.text.strip()]
    source_prefix = source_roots[0] if source_roots else ""

    out: dict[str, float] = {}
    for cls in root.iter("class"):
        filename = cls.get("filename") or ""
        if not filename:
            continue
        if source_prefix and not filename.startswith(source_prefix + "/"):
            full = f"{source_prefix}/{filename}"
        else:
            full = filename
        try:
            rate = float(cls.get("line-rate", "1.0")) * 100.0
        except ValueError:
            continue
        prev = out.get(full)
        if prev is None or rate < prev:
            out[full] = rate
    return out


class CoverageFloor(FitnessRule):
    """Flags source files whose recorded line coverage is below the floor."""

    name = "coverage-floor"
    remediation = REMEDIATION
    extensions = (".py",)

    #: Rule-specific knobs — instance attrs so ``from_config`` overrides them.
    floor_pct: float = DEFAULT_FLOOR_PCT
    coverage_report: str = DEFAULT_COVERAGE_REPORT

    @classmethod
    def from_config(
        cls,
        config: Mapping[str, Any],
        *,
        repo_root: Path | None = None,
    ) -> CoverageFloor:
        """Build from config, also reading ``floor_pct`` and ``coverage_report``."""
        rule = super().from_config(config, repo_root=repo_root)
        assert isinstance(rule, CoverageFloor)  # noqa: S101  # narrowing for mypy
        rule.floor_pct = float(config.get("floor_pct", DEFAULT_FLOOR_PCT))
        rule.coverage_report = str(config.get("coverage_report", DEFAULT_COVERAGE_REPORT))
        return rule

    def _report_path(self) -> Path:
        report = Path(self.coverage_report)
        return report if report.is_absolute() else self._repo_root / report

    def _below_floor(self) -> dict[str, float]:
        """Map of report-relative path → coverage for files under the floor."""
        coverage = parse_coverage_report(self._report_path())
        return {path: pct for path, pct in coverage.items() if pct < self.floor_pct}

    def enumerate_files(self) -> list[Path]:
        """Enumerate the below-floor files named in the coverage report.

        Overrides the default rglob walk: the rule's universe is the coverage
        report's contents, not the on-disk tree. Each below-floor entry is
        returned as a repo-root-anchored path so the inherited scope predicate
        (extensions + roots) still applies.
        """
        return [self._repo_root / path for path in self._below_floor()]

    def file_has_violation(self, path: Path) -> bool:
        """Every enumerated file is, by construction, below the floor."""
        rel = str(self._repo_relative(path))
        return rel in self._below_floor()


def build(
    config: Mapping[str, Any],
    *,
    repo_root: Path | None = None,
) -> CoverageFloor:
    """Factory the engine calls to bind this CORE check to a consumer's config."""
    return CoverageFloor.from_config(config, repo_root=repo_root)


def main(argv: list[str] | None = None) -> int:
    """CLI entry — supports ``--establish-baseline`` and ``--repo-root``."""
    return run_core_check(CoverageFloor, argv)


if __name__ == "__main__":
    import sys

    sys.exit(main())
