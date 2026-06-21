"""CORE check: suppressions_have_rationale — every silencer carries a reason.

Sibling of ``no_production_suppressions`` (which bans suppressions outright in
production source). This covers the *allowed-area* suppressions — tests,
scripts, tools — where a deliberate ``# noqa`` / ``# type: ignore`` / ``# nosec``
is sometimes the right call but a BARE one is debt. A suppression PASSES when
the same line carries non-empty text after the token (em-dash + sentence is the
canonical shape); a bare suppression FAILS.

Ported from kairix ``check-suppressions-have-rationale.sh`` (F3, via the
tc-agent-zone Python port) and re-expressed as a configurable, repo-agnostic
rule. The bare-suppression patterns are the rule's own shape
(``DEFAULT_BARE_PATTERNS``), overridable via a ``bare_patterns`` knob; the
consumer supplies ``roots`` / ``exempt_files`` via ``[tool.tc_fitness]``. No
repo paths are baked in.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from tc_fitness.core_checks import run_core_check
from tc_fitness.fitness_rule import FitnessRule
from tc_fitness.lib import remediation as _remediation

#: Bare-suppression patterns: each matches ``<token>`` followed only by optional
#: whitespace + end-of-line. Any trailing rationale text makes the line PASS.
#: The rule's own shape (ruff / mypy / bandit / coverage / Sonar), overridable.
DEFAULT_BARE_PATTERNS: tuple[str, ...] = (
    r"#\s*NOSONAR\s*$",
    r"#\s*+noqa(?::\s*+[A-Z0-9, ]++)?\s*+$",
    r"#\s*pragma:\s*no cover\s*$",
    r"#\s*type:\s*ignore(\[[A-Za-z0-9,_-]+\])?\s*$",
    r"#\s*nosec(\s+B\d+|:\s*B?\d+)?\s*$",
)

REMEDIATION = _remediation(
    fix=(
        "append a same-line rationale directly after the suppression token so "
        "the line documents WHY the rule is silenced (an em-dash + one-line "
        "reason is the canonical shape) — or delete the suppression and address "
        "the underlying warning."
    ),
    nxt="re-run this check to confirm the gate goes green.",
    run="python -m tc_fitness.core_checks.suppressions_have_rationale",
    passing="x = 1  # NOSONAR - internal log path; not user-controlled",
    forbidden="x = 1  # NOSONAR",
)


def _compile(patterns: Sequence[str]) -> tuple[re.Pattern[str], ...]:
    return tuple(re.compile(p) for p in patterns)


def file_has_bare_suppression(path: Path, compiled: Sequence[re.Pattern[str]]) -> bool:
    """True iff any line in ``path`` matches a bare-suppression pattern.

    Pure helper (the detection core) so tests assert on it directly. A read
    error is treated as "no violation".
    """
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return False
    return any(pat.search(line) for line in lines for pat in compiled)


class SuppressionsHaveRationale(FitnessRule):
    """Flags files holding a bare (rationale-free) lint/type/security suppression."""

    name = "suppressions-have-rationale"
    remediation = REMEDIATION
    extensions = (".py",)

    #: Rule-specific knob — the bare-suppression regexes; overridable per consumer.
    bare_patterns: tuple[str, ...] = DEFAULT_BARE_PATTERNS

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._compiled = _compile(self.bare_patterns)

    @classmethod
    def from_config(
        cls,
        config: Mapping[str, Any],
        *,
        repo_root: Path | None = None,
    ) -> SuppressionsHaveRationale:
        rule = super().from_config(config, repo_root=repo_root)
        assert isinstance(rule, SuppressionsHaveRationale)  # noqa: S101  # narrowing for mypy
        patterns = config.get("bare_patterns")
        rule.bare_patterns = tuple(patterns) if patterns is not None else DEFAULT_BARE_PATTERNS
        rule._compiled = _compile(rule.bare_patterns)
        return rule

    def file_has_violation(self, path: Path) -> bool:
        return file_has_bare_suppression(path, self._compiled)


def build(config: Mapping[str, Any], *, repo_root: Path | None = None) -> SuppressionsHaveRationale:
    """Factory the engine calls to bind this CORE check to a consumer's config."""
    return SuppressionsHaveRationale.from_config(config, repo_root=repo_root)


def main(argv: list[str] | None = None) -> int:
    """CLI entry — supports ``--establish-baseline`` and ``--repo-root``."""
    return run_core_check(SuppressionsHaveRationale, argv)


if __name__ == "__main__":
    import sys

    sys.exit(main())
