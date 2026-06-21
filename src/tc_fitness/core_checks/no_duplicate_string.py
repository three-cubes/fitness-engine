"""CORE check: no_duplicate_string — Sonar S1192 (the exemplar CORE module).

A string literal of >= ``min_length`` chars duplicated >= ``min_occurrences``
times in a single Python module is a refactor smell — the reader can't tell
whether the sites are coupled or coincidentally identical. Extracting to a
module-level ``UPPER_SNAKE_CASE`` constant makes the coupling explicit.

This is the COPY-PATTERN every other CORE check follows (the Port agents lift
this shape): a :class:`tc_fitness.fitness_rule.FitnessRule` subclass that reads
its repo-specific knobs (``min_length`` / ``min_occurrences`` and the
inherited ``roots`` / ``extensions`` / ``exempt_files``) from the consumer's
config, plus a ``build()`` factory and a ``main()`` wired through
:func:`tc_fitness.core_checks.run_core_check`.

Ported from tc-agent-zone ``scripts/checks/no_duplicate_string.py`` (itself
kairix F17) and re-expressed as a configurable, repo-agnostic rule: NO repo
paths, globs, or threshold literals are baked in — the defaults are
domain-intrinsic (S1192's own 10-char / 3-occurrence shape) and every one is
overridable via ``[tool.tc_fitness]``.
"""

from __future__ import annotations

import ast
from collections import Counter
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from tc_fitness.core_checks import run_core_check
from tc_fitness.fitness_rule import FitnessRule
from tc_fitness.lib import remediation as _remediation

#: S1192's own defaults — domain-intrinsic, not repo identity. Overridable.
DEFAULT_MIN_LENGTH = 10
DEFAULT_MIN_OCCURRENCES = 3

REMEDIATION = _remediation(
    fix=(
        'declare `_<NAME> = "<the literal>"` near the top of the module and '
        "replace every occurrence of the literal with the constant — this makes "
        "the coupling between sites explicit and gives renames a single edit site."
    ),
    nxt="re-run this check to confirm it goes green.",
    run="python -m tc_fitness.core_checks.no_duplicate_string",
    passing='_ERROR_BAD_QUERY = "search query must be a non-empty string"  # used 3x',
    forbidden='raise ValueError("search query must be a non-empty string")  # repeated 3x inline',
)


def _collect_docstring_ids(tree: ast.AST) -> set[int]:
    """Object-ids of the docstring Constant nodes (excluded from counting)."""
    out: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Module | ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef) and node.body:
            first = node.body[0]
            if (
                isinstance(first, ast.Expr)
                and isinstance(first.value, ast.Constant)
                and isinstance(first.value.value, str)
            ):
                out.add(id(first.value))
    return out


def module_has_duplicate(path: Path, *, min_length: int, min_occurrences: int) -> bool:
    """True iff ``path`` holds a str literal >= ``min_length`` repeated >= ``min_occurrences`` times.

    Pure helper (the detection core) so tests can assert on it directly:
    parses the module, counts non-docstring non-blank string Constants, and
    flags the file when any value crosses the occurrence threshold. A syntax /
    decode error is treated as "no violation" (another check owns unparseable
    files).
    """
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (SyntaxError, UnicodeDecodeError):
        return False
    docstring_ids = _collect_docstring_ids(tree)
    counts: Counter[str] = Counter()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
            continue
        if id(node) in docstring_ids:
            continue
        value = node.value
        if len(value) < min_length or not value.strip():
            continue
        counts[value] += 1
    return any(c >= min_occurrences for c in counts.values())


class NoDuplicateString(FitnessRule):
    """Flags modules with a duplicated string literal (Sonar S1192)."""

    name = "no-duplicate-string"
    remediation = REMEDIATION
    extensions = (".py",)

    #: Rule-specific thresholds. Instance attributes so ``from_config`` can
    #: override them per consumer; class defaults are S1192's own shape.
    min_length: int = DEFAULT_MIN_LENGTH
    min_occurrences: int = DEFAULT_MIN_OCCURRENCES

    @classmethod
    def from_config(
        cls,
        config: Mapping[str, Any],
        *,
        repo_root: Path | None = None,
    ) -> NoDuplicateString:
        """Build from config, also reading the two rule-specific thresholds.

        Extends the base ``from_config`` (which handles ``roots`` /
        ``extensions`` / ``exempt_files`` / ``name``) with ``min_length`` and
        ``min_occurrences``.
        """
        rule = super().from_config(config, repo_root=repo_root)
        assert isinstance(rule, NoDuplicateString)  # noqa: S101  # narrowing for mypy
        rule.min_length = int(config.get("min_length", DEFAULT_MIN_LENGTH))
        rule.min_occurrences = int(config.get("min_occurrences", DEFAULT_MIN_OCCURRENCES))
        return rule

    def file_has_violation(self, path: Path) -> bool:
        return module_has_duplicate(
            path,
            min_length=self.min_length,
            min_occurrences=self.min_occurrences,
        )


def build(
    config: Mapping[str, Any],
    *,
    repo_root: Path | None = None,
) -> NoDuplicateString:
    """Factory the engine calls to bind this CORE check to a consumer's config."""
    return NoDuplicateString.from_config(config, repo_root=repo_root)


def main(argv: list[str] | None = None) -> int:
    """CLI entry — supports ``--establish-baseline`` and ``--repo-root``."""
    return run_core_check(NoDuplicateString, argv)


if __name__ == "__main__":
    import sys

    sys.exit(main())
