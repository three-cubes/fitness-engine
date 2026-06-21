"""CORE check: unused_params_named — Sonar S1172.

A function parameter never read in the body is either dead code (delete it from
the signature) or a Protocol-required position the implementation does not need
(rename to ``_unused`` so the reader sees the intent and the linter stops
flagging it). The convention: rename to ``_``-prefixed if a Protocol requires
the slot; otherwise delete it.

Ported from kairix ``scripts/checks/check_unused_params_named.py`` (F19) and
re-expressed as a configurable, repo-agnostic rule. The detection is pure AST
with no domain-intrinsic threshold; the consumer supplies ``roots`` /
``exempt_files`` via ``[tool.tc_fitness]``. No repo paths or globs are baked in.
"""

from __future__ import annotations

import ast
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from tc_fitness.core_checks import run_core_check
from tc_fitness.fitness_rule import FitnessRule
from tc_fitness.lib import remediation as _remediation

_EXEMPT_NAMES = frozenset({"self", "cls"})
_ABSTRACT_DECORATORS = frozenset({"abstractmethod", "abstractproperty", "overload"})

REMEDIATION = _remediation(
    fix=(
        "if the unused parameter is required by a Protocol/abstract-base "
        "position, rename it with a leading underscore (_unused); if it is "
        "not load-bearing, delete it from the signature outright."
    ),
    nxt="re-run this check to confirm the gate goes green.",
    run="python -m tc_fitness.core_checks.unused_params_named",
    passing="def handle(event: Event, _context: Context) -> Result: ...",
    forbidden="def handle(event: Event, context: Context) -> Result:  # context never read",
)


def _decorator_name(node: ast.expr) -> str | None:
    if isinstance(node, ast.Attribute):
        return node.attr
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Call):
        return _decorator_name(node.func)
    return None


def _is_not_implemented_raise(stmt: ast.stmt) -> bool:
    if not isinstance(stmt, ast.Raise) or not isinstance(stmt.exc, ast.Name | ast.Call):
        return False
    target = stmt.exc.func if isinstance(stmt.exc, ast.Call) else stmt.exc
    return isinstance(target, ast.Name) and target.id == "NotImplementedError"


def _is_abstract_or_overload(func: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    """True if decorated abstract/overload, or the body is a stub.

    Stub bodies: a lone ``...`` / ``pass`` / ``raise NotImplementedError`` /
    docstring, or a docstring followed by one of those — the signature is the
    contract.
    """
    if any(_decorator_name(d) in _ABSTRACT_DECORATORS for d in func.decorator_list):
        return True
    body = func.body
    if len(body) == 1:
        only = body[0]
        if isinstance(only, ast.Pass):
            return True
        if isinstance(only, ast.Expr) and isinstance(only.value, ast.Constant):
            return True
        if _is_not_implemented_raise(only):
            return True
    if len(body) == 2:
        first, second = body
        first_is_doc = (
            isinstance(first, ast.Expr)
            and isinstance(first.value, ast.Constant)
            and isinstance(first.value.value, str)
        )
        if first_is_doc:
            if isinstance(second, ast.Pass):
                return True
            if (
                isinstance(second, ast.Expr)
                and isinstance(second.value, ast.Constant)
                and second.value.value is ...
            ):
                return True
            if _is_not_implemented_raise(second):
                return True
    return False


def _is_property_setter(func: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    return any(isinstance(d, ast.Attribute) and d.attr == "setter" for d in func.decorator_list)


def _names_read(body: list[ast.stmt]) -> set[str]:
    refs: set[str] = set()
    for stmt in body:
        for node in ast.walk(stmt):
            if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
                refs.add(node.id)
    return refs


def _function_has_unused_param(func: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    if _is_abstract_or_overload(func) or _is_property_setter(func):
        return False
    args = func.args
    all_args = [*args.posonlyargs, *args.args, *args.kwonlyargs]
    if not all_args:
        return False
    refs = _names_read(func.body)
    for arg in all_args:
        name = arg.arg
        if name in _EXEMPT_NAMES or name.startswith("_"):
            continue
        if name not in refs:
            return True
    return False


def module_has_unused_param(path: Path) -> bool:
    """True iff any function in ``path`` has an unused, non-underscore parameter.

    Pure helper (the detection core). A syntax / decode error is treated as
    "no violation" — another check owns unparseable files.
    """
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (SyntaxError, UnicodeDecodeError):
        return False
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and _function_has_unused_param(node):
            return True
    return False


class UnusedParamsNamed(FitnessRule):
    """Flags files holding an unused, non-underscore-prefixed parameter (S1172)."""

    name = "unused-params-named"
    remediation = REMEDIATION
    extensions = (".py",)

    def file_has_violation(self, path: Path) -> bool:
        return module_has_unused_param(path)


def build(config: Mapping[str, Any], *, repo_root: Path | None = None) -> FitnessRule:
    """Factory the engine calls to bind this CORE check to a consumer's config."""
    return UnusedParamsNamed.from_config(config, repo_root=repo_root)


def main(argv: list[str] | None = None) -> int:
    """CLI entry — supports ``--establish-baseline`` and ``--repo-root``."""
    return run_core_check(UnusedParamsNamed, argv)


if __name__ == "__main__":
    import sys

    sys.exit(main())
