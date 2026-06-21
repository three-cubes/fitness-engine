"""CORE check: mutation_survival_ratchet — the mutation report keeps its shape.

A mutation report records, per package, how many injected mutants the suite
KILLED versus how many SURVIVED. A rising survival rate means the suite is
getting weaker. The full ratchet compares a current report against a frozen
baseline with commit-message overrides — but that comparison is git-coupled and
override-grammar-coupled, so the engine ports only the SCOPE the task names:
the file-shape contract plus the ``--allow-missing-current`` adoption pass.

What this CORE rule enforces:

* the baseline report exists and obeys the contract (``schema_version == 1`` and
  a ``packages`` object) — a malformed baseline is a violation;
* the current report, WHEN PRESENT, obeys the same contract;
* the current report being ABSENT is tolerated (the adoption pass): a consumer
  wiring the ratchet before any mutation run has produced a report still passes
  the shape gate.

The git-diff comparison + override grammar are deliberately NOT ported (they are
repo-coupled); a consumer that wants enforcement runs its own comparison on the
shape this rule guarantees.

Ported from tc-agent-zone ``scripts/checks/mutation_survival_ratchet.py`` —
re-expressed as a configurable, repo-agnostic rule. The two report paths are
CONFIG; nothing here names a repo.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from tc_fitness.core_checks import run_core_check
from tc_fitness.fitness_rule import FitnessRule
from tc_fitness.lib import remediation as _remediation

#: The schema version a mutation report MUST declare. Domain-intrinsic.
REQUIRED_SCHEMA_VERSION = 1

#: Default report locations relative to the repo root. Overridable per consumer.
DEFAULT_BASELINE_REPORT = ".architecture/baseline/mutation-survival-rates.json"
DEFAULT_CURRENT_REPORT = ".mutation/mutation-survival-rates.json"

REMEDIATION = _remediation(
    fix=(
        "make the mutation report obey its contract: valid JSON, "
        "schema_version of 1, and a packages object mapping each package to its "
        "survived/killed counts. Regenerate the report if it is stale or "
        "hand-edited into an invalid shape."
    ),
    nxt="re-run this check to confirm the report shape validates.",
    run="python -m tc_fitness.core_checks.mutation_survival_ratchet",
    passing='{"schema_version": 1, "packages": {"pkg": {"survived": 0, "killed": 9}}}',
    forbidden='{"schema_version": 2, "packages": []}  (wrong version, packages not an object)',
)


def report_is_malformed(path: Path) -> bool:
    """True iff the report at ``path`` violates the shape contract.

    A non-existent file is NOT judged here (the caller decides whether a missing
    report is tolerated). A present file must be valid JSON declaring
    ``schema_version == REQUIRED_SCHEMA_VERSION`` with a ``packages`` mapping;
    anything else is malformed.
    """
    if not path.exists():
        return False
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return True
    if not isinstance(data, dict):
        return True
    if data.get("schema_version") != REQUIRED_SCHEMA_VERSION:
        return True
    return not isinstance(data.get("packages", {}), dict)


class MutationSurvivalRatchet(FitnessRule):
    """Flags a mutation report that breaks its file-shape contract."""

    name = "mutation-survival-ratchet"
    remediation = REMEDIATION
    extensions = (".json",)

    #: Rule-specific knobs — instance attrs so ``from_config`` overrides them.
    baseline_report: str = DEFAULT_BASELINE_REPORT
    current_report: str = DEFAULT_CURRENT_REPORT
    #: When True, an absent current report passes (the adoption pass). The
    #: baseline must always exist and validate.
    allow_missing_current: bool = True

    @classmethod
    def from_config(
        cls,
        config: Mapping[str, Any],
        *,
        repo_root: Path | None = None,
    ) -> MutationSurvivalRatchet:
        """Build from config, reading the report paths + the adoption flag."""
        rule = super().from_config(config, repo_root=repo_root)
        assert isinstance(rule, MutationSurvivalRatchet)  # noqa: S101  # narrowing for mypy
        rule.baseline_report = str(config.get("baseline_report", DEFAULT_BASELINE_REPORT))
        rule.current_report = str(config.get("current_report", DEFAULT_CURRENT_REPORT))
        rule.allow_missing_current = bool(config.get("allow_missing_current", True))
        return rule

    def _abs(self, rel: str) -> Path:
        path = Path(rel)
        return path if path.is_absolute() else self._repo_root / path

    def enumerate_files(self) -> list[Path]:
        """The two report artifacts this rule judges: baseline + current.

        The baseline is always enumerated (its absence is itself a violation, so
        ``file_has_violation`` can flag it). The current report is enumerated
        only when it exists OR when the adoption flag is off — when the flag is
        on and the file is absent, it is dropped so nothing flags it.
        """
        out = [self._abs(self.baseline_report)]
        current = self._abs(self.current_report)
        if current.exists() or not self.allow_missing_current:
            out.append(current)
        return out

    def is_in_scope(self, rel: str) -> bool:
        """Admit the configured report paths regardless of location."""
        return True

    def file_has_violation(self, path: Path) -> bool:
        # Any report that reaches this point is REQUIRED to exist (the adoption
        # pass drops a tolerated-absent current from enumeration). An absent
        # required report is a violation; a present one must validate.
        if not path.exists():
            return True
        return report_is_malformed(path)


def build(
    config: Mapping[str, Any],
    *,
    repo_root: Path | None = None,
) -> MutationSurvivalRatchet:
    """Factory the engine calls to bind this CORE check to a consumer's config."""
    return MutationSurvivalRatchet.from_config(config, repo_root=repo_root)


def main(argv: list[str] | None = None) -> int:
    """CLI entry — supports ``--establish-baseline`` and ``--repo-root``."""
    return run_core_check(MutationSurvivalRatchet, argv)


if __name__ == "__main__":
    import sys

    sys.exit(main())
