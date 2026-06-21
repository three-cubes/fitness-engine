"""CORE check: adr_number_unique — numbered decision records have unique numbers.

Architecture Decision Records (and any numbered-doc series) are cited by their
numeric prefix; a collision breaks navigation, citation, and every gate that
resolves a record by id. This rule scans the configured decision directory and
FAILS when two files share a number.

Built fresh for the v0.6.0 CORE set from tc-agent-zone
``scripts/checks/adr_number_unique.py`` and re-expressed as a configurable,
repo-agnostic rule. What was repo-specific — the directory the records live in
and the filename pattern that carries the number — is consumer config. The
engine ships a generic ``ADR-<NNN>-<slug>`` default pattern, overridable for
any numbered-doc convention.

This rule is a CROSS-FILE invariant (a number, not a file, is the unit of
violation), so it overrides :meth:`collect_violations` rather than implementing
a per-file predicate: a file is "in violation" when it shares its number with
another file. Baseline gating still applies — a net-new colliding file fails;
pre-existing collisions can be grandfathered (then paid down).
"""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from tc_fitness.core_checks import run_core_check
from tc_fitness.fitness_rule import FitnessRule
from tc_fitness.lib import remediation as _remediation

#: Generic numbered-record default — captures the number in group 1. Overridable.
DEFAULT_RECORD_PATTERN = r"^ADR-(\d{3})-.+\.md$"
#: Directory the records live under, repo-relative. Consumer config in practice.
DEFAULT_RECORD_DIR = "docs/decisions"

REMEDIATION = _remediation(
    fix=(
        "renumber the newer file to the next free number, update its `id:` field "
        "and heading, and sweep every reference across the repo (both the "
        "file-path form and the bare-number form). Keep the older file's number."
    ),
    nxt="re-run this check to confirm it goes green.",
    run="python -m tc_fitness.core_checks.adr_number_unique",
    passing="docs/decisions/ADR-041-some-decision.md  (041 used by exactly one file)",
    forbidden="ADR-041-foo.md AND ADR-041-bar.md  (041 used by two files)",
)


def find_collisions(
    record_dir: Path,
    *,
    pattern: re.Pattern[str],
) -> dict[str, list[Path]]:
    """Pure helper: map each number used >1 time to the files that use it.

    Returns only the colliding numbers (those with two or more files). The file
    list per number is sorted for stable output.
    """
    by_number: dict[str, list[Path]] = defaultdict(list)
    if not record_dir.is_dir():
        return {}
    for path in sorted(record_dir.iterdir()):
        if not path.is_file():
            continue
        match = pattern.match(path.name)
        if match:
            by_number[match.group(1)].append(path)
    return {num: sorted(paths) for num, paths in by_number.items() if len(paths) > 1}


class AdrNumberUnique(FitnessRule):
    """Flags numbered decision records that share a number (a cross-file invariant)."""

    name = "adr-number-unique"
    remediation = REMEDIATION
    extensions = (".md",)

    #: Rule-specific config (instance attrs; from_config overrides per consumer).
    record_dir: str = DEFAULT_RECORD_DIR
    record_pattern: re.Pattern[str] = re.compile(DEFAULT_RECORD_PATTERN)

    @classmethod
    def from_config(
        cls,
        config: Mapping[str, Any],
        *,
        repo_root: Path | None = None,
    ) -> AdrNumberUnique:
        rule = super().from_config(config, repo_root=repo_root)
        assert isinstance(rule, AdrNumberUnique)  # noqa: S101  # narrowing for mypy
        rule.record_dir = str(config.get("record_dir", DEFAULT_RECORD_DIR))
        rule.record_pattern = re.compile(str(config.get("record_pattern", DEFAULT_RECORD_PATTERN)))
        return rule

    def file_has_violation(self, path: Path) -> bool:
        """Unused for this rule — collisions are cross-file (see collect_violations)."""
        return False

    def collect_violations(self) -> set[Path]:
        """Override: every file sharing its number with another is in violation."""
        record_dir = self._repo_root / self.record_dir
        collisions = find_collisions(record_dir, pattern=self.record_pattern)
        out: set[Path] = set()
        for paths in collisions.values():
            for p in paths:
                out.add(self._repo_relative(p))
        return out


def build(
    config: Mapping[str, Any],
    *,
    repo_root: Path | None = None,
) -> AdrNumberUnique:
    """Factory the engine calls to bind this CORE check to a consumer's config."""
    return AdrNumberUnique.from_config(config, repo_root=repo_root)


def main(argv: list[str] | None = None) -> int:
    """CLI entry — supports ``--establish-baseline`` and ``--repo-root``."""
    return run_core_check(AdrNumberUnique, argv)


if __name__ == "__main__":
    import sys

    sys.exit(main())
