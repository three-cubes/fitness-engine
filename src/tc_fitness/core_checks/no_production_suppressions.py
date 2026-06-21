"""CORE check: no_production_suppressions — ADR-010 D7 (Sonar/lint silencers).

Production code must be correct, not silenced. A lint / coverage / Sonar
suppression in production source (``# noqa:``, ``# NOSONAR``,
``# pragma: no cover``) is an escape hatch: if a finding is wrong, fix the
structure or delete the dead code rather than tagging it for the linter to
ignore. Tooling and test scaffolding are NOT production code, so the consumer
supplies an exempt-prefix set and the test-file basename rule.

Ported from tc-agent-zone ``scripts/checks/no_production_suppressions.py`` and
re-expressed as a configurable, repo-agnostic rule. The suppression tokens are
the rule's own shape (domain-intrinsic ``DEFAULT_SUPPRESSION_PATTERNS``),
overridable via a ``suppression_patterns`` knob; the exempt path prefixes and
test-basename regex come from config. No repo paths are baked in.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from tc_fitness.core_checks import run_core_check
from tc_fitness.fitness_rule import FitnessRule
from tc_fitness.lib import remediation as _remediation

#: The suppression markers — the rule's own shape, not repo identity. Overridable.
DEFAULT_SUPPRESSION_PATTERNS: tuple[str, ...] = (
    "# pragma: no cover",
    "# NOSONAR",
    "// NOSONAR",
    "# noqa:",
    "// noqa:",
)

#: Python test-file basename convention — domain-intrinsic, overridable.
DEFAULT_TEST_FILE_REGEX = r"^(test_.+\.py|.+_test\.py)$"

REMEDIATION = _remediation(
    fix=(
        "remove the suppression and address the underlying finding (refactor, "
        "delete dead code, or fix the bug); if the file is genuinely tooling "
        "not production logic, move it under an exempt path or list it in "
        "exempt_files."
    ),
    nxt="re-run this check to confirm the gate goes green.",
    run="python -m tc_fitness.core_checks.no_production_suppressions",
    passing="result = parse(payload)  # finding fixed by validating payload upstream",
    forbidden="result = parse(payload)  # noqa: BLE001",
)


def file_contains_suppression(path: Path, patterns: Sequence[str]) -> bool:
    """True iff any line in ``path`` contains one of ``patterns`` (substring).

    Pure helper (the detection core) so tests assert on it directly. A decode /
    read error is treated as "no violation".
    """
    try:
        text = path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        return False
    return any(pat in line for line in text.splitlines() for pat in patterns)


class NoProductionSuppressions(FitnessRule):
    """Flags production-code lines carrying a lint/coverage/Sonar suppression."""

    name = "no-production-suppressions"
    remediation = REMEDIATION
    extensions = (".py",)

    #: Rule-specific knobs — overridable per consumer.
    suppression_patterns: tuple[str, ...] = DEFAULT_SUPPRESSION_PATTERNS
    #: Repo-relative path prefixes whose files are exempt (tooling / tests / docs).
    exempt_prefixes: tuple[str, ...] = ()
    #: Basename regex marking a file as a test (exempt). Default: Python convention.
    test_file_regex: str = DEFAULT_TEST_FILE_REGEX

    @classmethod
    def from_config(
        cls,
        config: Mapping[str, Any],
        *,
        repo_root: Path | None = None,
    ) -> NoProductionSuppressions:
        rule = super().from_config(config, repo_root=repo_root)
        assert isinstance(rule, NoProductionSuppressions)  # noqa: S101  # narrowing for mypy
        patterns = config.get("suppression_patterns")
        rule.suppression_patterns = tuple(patterns) if patterns is not None else DEFAULT_SUPPRESSION_PATTERNS
        prefixes = config.get("exempt_prefixes")
        rule.exempt_prefixes = tuple(prefixes) if prefixes is not None else ()
        rule.test_file_regex = str(config.get("test_file_regex", DEFAULT_TEST_FILE_REGEX))
        return rule

    def is_in_scope(self, rel: str) -> bool:
        """Extension-in-scope AND not under an exempt prefix AND not a test file."""
        if not super().is_in_scope(rel):
            return False
        if any(rel.startswith(p) for p in self.exempt_prefixes):
            return False
        basename = rel.rsplit("/", 1)[-1]
        return not re.match(self.test_file_regex, basename)

    def file_has_violation(self, path: Path) -> bool:
        return file_contains_suppression(path, self.suppression_patterns)


def build(config: Mapping[str, Any], *, repo_root: Path | None = None) -> NoProductionSuppressions:
    """Factory the engine calls to bind this CORE check to a consumer's config."""
    return NoProductionSuppressions.from_config(config, repo_root=repo_root)


def main(argv: list[str] | None = None) -> int:
    """CLI entry — supports ``--establish-baseline`` and ``--repo-root``."""
    return run_core_check(NoProductionSuppressions, argv)


if __name__ == "__main__":
    import sys

    sys.exit(main())
