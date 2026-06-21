"""CORE check: cognitive_complexity — Sonar S3776 (Campbell).

Cognitive complexity measures how hard a function is to *read*, not how hard
it is to test. The score climbs with each branch (``if`` / ``elif`` / ``else``
/ ``for`` / ``while`` / ``try`` / ``except`` / ternary / boolean operator) and
is amplified by nesting depth — a triple-nested ``if`` is harder to follow
than three sequential ones. A function scoring above the threshold is flagged;
the file is the unit baselined.

Ported from kairix ``scripts/checks/check_cognitive_complexity.py`` (F16) and
re-expressed as a configurable, repo-agnostic rule: the only domain-intrinsic
number is S3776's own default ceiling (15), exposed as a ``threshold`` knob the
consumer overrides via ``[tool.tc_fitness]``. No repo paths or globs are baked
in — the consumer supplies ``roots`` / ``exempt_files``.
"""

from __future__ import annotations

import ast
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from tc_fitness.core_checks import run_core_check
from tc_fitness.fitness_rule import FitnessRule
from tc_fitness.lib import remediation as _remediation

#: S3776's own default ceiling — domain-intrinsic, not repo identity. Overridable.
DEFAULT_THRESHOLD = 15

REMEDIATION = _remediation(
    fix=(
        "pick the most-nested branch in the flagged function and extract it "
        "into a named helper; or replace an if/elif chain with a dispatch "
        "dict; or invert a guard so the happy path returns early."
    ),
    nxt="re-run this check to confirm the gate goes green.",
    run="python -m tc_fitness.core_checks.cognitive_complexity",
    passing="handler = _HANDLERS.get(cmd, _default); return handler(args)",
    forbidden="nested if/elif chains scoring above the cognitive-complexity ceiling",
)


class _Scorer(ast.NodeVisitor):
    """Accumulate a cognitive-complexity score for one function body.

    The ``nesting`` counter rises on every branch construct and is added to
    each subsequent branch encountered inside it (the nesting amplifier).
    """

    def __init__(self) -> None:
        self.score = 0
        self.nesting = 0

    def _bump(self) -> None:
        self.score += 1 + self.nesting

    def _walk_nested(self, body: list[ast.stmt]) -> None:
        self.nesting += 1
        for child in body:
            self.visit(child)
        self.nesting -= 1

    def visit_If(self, node: ast.If) -> None:
        self._bump()
        self._walk_nested(node.body)
        if node.orelse:
            # An ``elif`` is a single nested If in orelse — recurse so it is
            # counted once; a plain ``else`` body bumps and nests.
            if len(node.orelse) == 1 and isinstance(node.orelse[0], ast.If):
                self.visit(node.orelse[0])
            else:
                self._bump()
                self._walk_nested(node.orelse)

    def visit_For(self, node: ast.For) -> None:
        self._bump()
        self._walk_nested(node.body)
        if node.orelse:
            self._walk_nested(node.orelse)

    def visit_AsyncFor(self, node: ast.AsyncFor) -> None:
        self._bump()
        self._walk_nested(node.body)
        if node.orelse:
            self._walk_nested(node.orelse)

    def visit_While(self, node: ast.While) -> None:
        self._bump()
        self._walk_nested(node.body)
        if node.orelse:
            self._walk_nested(node.orelse)

    def visit_Try(self, node: ast.Try) -> None:
        self._bump()
        self._walk_nested(node.body)
        for handler in node.handlers:
            self._bump()
            self._walk_nested(handler.body)
        if node.orelse:
            self._walk_nested(node.orelse)
        if node.finalbody:
            self._walk_nested(node.finalbody)

    def visit_With(self, node: ast.With) -> None:
        # ``with`` does not branch, so it adds nothing to the score.
        self.generic_visit(node)

    def visit_AsyncWith(self, node: ast.AsyncWith) -> None:
        self.generic_visit(node)

    def visit_BoolOp(self, node: ast.BoolOp) -> None:
        # A chain ``a and b and c`` has two operators → +2 (flat, no amplifier).
        self.score += max(len(node.values) - 1, 0)
        self.generic_visit(node)

    def visit_IfExp(self, node: ast.IfExp) -> None:
        self._bump()
        self.generic_visit(node)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self.generic_visit(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self.generic_visit(node)


def _score_function(func: ast.FunctionDef | ast.AsyncFunctionDef) -> int:
    scorer = _Scorer()
    for stmt in func.body:
        scorer.visit(stmt)
    return scorer.score


def module_over_threshold(path: Path, *, threshold: int) -> bool:
    """True iff any function in ``path`` scores above ``threshold``.

    Pure helper (the detection core) so tests assert on it directly. A syntax /
    decode error is treated as "no violation" — another check owns unparseable
    files.
    """
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (SyntaxError, UnicodeDecodeError):
        return False
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and _score_function(node) > threshold:
            return True
    return False


class CognitiveComplexity(FitnessRule):
    """Flags files holding a function above the cognitive-complexity ceiling (S3776)."""

    name = "cognitive-complexity"
    remediation = REMEDIATION
    extensions = (".py",)

    #: Rule-specific knob — S3776's own ceiling; overridable per consumer.
    threshold: int = DEFAULT_THRESHOLD

    @classmethod
    def from_config(
        cls,
        config: Mapping[str, Any],
        *,
        repo_root: Path | None = None,
    ) -> CognitiveComplexity:
        rule = super().from_config(config, repo_root=repo_root)
        assert isinstance(rule, CognitiveComplexity)  # noqa: S101  # narrowing for mypy
        rule.threshold = int(config.get("threshold", DEFAULT_THRESHOLD))
        return rule

    def file_has_violation(self, path: Path) -> bool:
        return module_over_threshold(path, threshold=self.threshold)


def build(config: Mapping[str, Any], *, repo_root: Path | None = None) -> CognitiveComplexity:
    """Factory the engine calls to bind this CORE check to a consumer's config."""
    return CognitiveComplexity.from_config(config, repo_root=repo_root)


def main(argv: list[str] | None = None) -> int:
    """CLI entry — supports ``--establish-baseline`` and ``--repo-root``."""
    return run_core_check(CognitiveComplexity, argv)


if __name__ == "__main__":
    import sys

    sys.exit(main())
