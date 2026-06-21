"""CORE check: empty_body_intent — Sonar S1186.

A function body that is exactly ``pass`` or ``...`` with no documentation is a
confusion vector: the reader can't tell whether it's an abstract contract, a
deliberate no-op satisfying a Protocol, or an accidentally-truncated function.
The fix: add a one-line docstring describing the contract, or an intent comment
explaining why the body is genuinely empty.

Ported from kairix ``scripts/checks/check_empty_body_intent.py`` (F20) and
re-expressed as a configurable, repo-agnostic rule. The intent-comment marker
defaults to the conventional phrase but is overridable via a ``marker`` knob;
the consumer supplies ``roots`` / ``exempt_files`` via ``[tool.tc_fitness]``.
No repo paths or globs are baked in.
"""

from __future__ import annotations

import ast
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from tc_fitness.core_checks import run_core_check
from tc_fitness.fitness_rule import FitnessRule
from tc_fitness.lib import remediation as _remediation

#: The conventional intent-comment phrase — domain-intrinsic, overridable.
DEFAULT_MARKER = "Intentionally empty"

_ABSTRACT_DECORATORS = frozenset({"abstractmethod", "abstractproperty", "overload"})

REMEDIATION = _remediation(
    fix=(
        "add either a one-line docstring describing the Protocol contract the "
        "function satisfies, or an intent comment explaining why the body is "
        "genuinely a no-op."
    ),
    nxt="re-run this check to confirm the gate goes green.",
    run="python -m tc_fitness.core_checks.empty_body_intent",
    passing='def on_event(self, e): """No-op default; strategies override."""',
    forbidden="def on_event(self, e): pass  # no docstring, no intent comment",
)


def _decorator_name(node: ast.expr) -> str | None:
    if isinstance(node, ast.Attribute):
        return node.attr
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Call):
        return _decorator_name(node.func)
    return None


def _is_abstract_or_overload(func: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    return any(_decorator_name(d) in _ABSTRACT_DECORATORS for d in func.decorator_list)


def _has_docstring(func: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    if not func.body:
        return False
    first = func.body[0]
    return (
        isinstance(first, ast.Expr)
        and isinstance(first.value, ast.Constant)
        and isinstance(first.value.value, str)
    )


def _is_ellipsis_expr(stmt: ast.stmt) -> bool:
    return isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Constant) and stmt.value.value is ...


def _is_empty_body(func: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    """True if the body is exactly ``pass`` / ``...`` / ``docstring + pass|...``."""
    body = func.body
    if len(body) == 1:
        only = body[0]
        return isinstance(only, ast.Pass) or _is_ellipsis_expr(only)
    if len(body) == 2:
        first, second = body
        first_is_doc = (
            isinstance(first, ast.Expr)
            and isinstance(first.value, ast.Constant)
            and isinstance(first.value.value, str)
        )
        if first_is_doc:
            return isinstance(second, ast.Pass) or _is_ellipsis_expr(second)
    return False


def _has_intent_comment(
    func: ast.FunctionDef | ast.AsyncFunctionDef,
    source_lines: list[str],
    marker: str,
) -> bool:
    """True if ``marker`` appears in the function span or the line above ``def``."""
    start = (func.lineno or 1) - 1
    end = func.end_lineno or func.lineno or 1
    snippet = "\n".join(source_lines[max(start - 1, 0) : end])
    return marker in snippet


def _function_violates(
    func: ast.FunctionDef | ast.AsyncFunctionDef,
    source_lines: list[str],
    marker: str,
) -> bool:
    if _is_abstract_or_overload(func):
        return False
    if not _is_empty_body(func):
        return False
    if _has_docstring(func):
        return False
    return not _has_intent_comment(func, source_lines, marker)


def module_has_undocumented_empty_body(path: Path, *, marker: str) -> bool:
    """True iff any function in ``path`` has an undocumented empty body (S1186).

    Pure helper (the detection core). A syntax / decode error is treated as
    "no violation" — another check owns unparseable files.
    """
    try:
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(path))
    except (SyntaxError, UnicodeDecodeError):
        return False
    source_lines = source.splitlines()
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and _function_violates(
            node, source_lines, marker
        ):
            return True
    return False


class EmptyBodyIntent(FitnessRule):
    """Flags files holding an undocumented empty function body (Sonar S1186)."""

    name = "empty-body-intent"
    remediation = REMEDIATION
    extensions = (".py",)

    #: Rule-specific knob — the intent-comment phrase; overridable per consumer.
    marker: str = DEFAULT_MARKER

    @classmethod
    def from_config(
        cls,
        config: Mapping[str, Any],
        *,
        repo_root: Path | None = None,
    ) -> EmptyBodyIntent:
        rule = super().from_config(config, repo_root=repo_root)
        assert isinstance(rule, EmptyBodyIntent)  # noqa: S101  # narrowing for mypy
        rule.marker = str(config.get("marker", DEFAULT_MARKER))
        return rule

    def file_has_violation(self, path: Path) -> bool:
        return module_has_undocumented_empty_body(path, marker=self.marker)


def build(config: Mapping[str, Any], *, repo_root: Path | None = None) -> EmptyBodyIntent:
    """Factory the engine calls to bind this CORE check to a consumer's config."""
    return EmptyBodyIntent.from_config(config, repo_root=repo_root)


def main(argv: list[str] | None = None) -> int:
    """CLI entry — supports ``--establish-baseline`` and ``--repo-root``."""
    return run_core_check(EmptyBodyIntent, argv)


if __name__ == "__main__":
    import sys

    sys.exit(main())
