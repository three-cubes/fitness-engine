"""CORE check: actionable_feedback — every emitted check error is agent-actionable.

A fitness check whose failure message says only "validation failed" leaves the
next agent nowhere to go. Every error a check appends to its failure list MUST
carry an action marker (``fix:`` / ``next:`` / ``run:``) so the reader knows the
corrective action, the follow-up, and the exact re-verify command.

Detection is AST-based: it walks each in-scope module for ``<errors>.append(...)``
/ ``<errors>.extend(...)`` calls (any variable whose name contains ``error``) and
flags a literal string argument — including a flat f-string's literal parts —
that contains none of the action markers.

Ported from tc-agent-zone ``scripts/checks/actionable_feedback.py`` and
re-expressed as a configurable, repo-agnostic rule: the scan roots, in-scope
extensions, exempt files and the action markers themselves all arrive from the
consumer's ``[tool.tc_fitness]`` config. The donor hardcoded ``scripts/checks``
and three exempt dispatcher filenames; this module bakes in NONE.
"""

from __future__ import annotations

import ast
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from tc_fitness.core_checks import run_core_check
from tc_fitness.fitness_rule import FitnessRule
from tc_fitness.lib import remediation as _remediation

#: The action markers an agent-actionable error message carries. Domain-
#: intrinsic (the fix/next/run affordance shape), overridable via config.
DEFAULT_ACTION_MARKERS: tuple[str, ...] = ("fix:", "next:", "run:")

REMEDIATION = _remediation(
    fix=(
        "include an agent-actionable correction in the emitted error — at "
        "least one of fix: / next: / run: naming the corrective action, the "
        "follow-up, and the exact command to re-verify."
    ),
    nxt="re-run this check to confirm the message now carries a marker.",
    run="python -m tc_fitness.core_checks.actionable_feedback",
    passing='errors.append(f"{rel}: bad; fix: rename it; next: rerun the gate")',
    forbidden='errors.append(f"{rel}: validation failed")',
)


def _literal_text(node: ast.AST) -> str | None:
    """The literal string an arg node carries (Constant or flat f-string), else None."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        parts = [v.value for v in node.values if isinstance(v, ast.Constant) and isinstance(v.value, str)]
        return "".join(parts)
    return None


def _is_error_append_call(node: ast.AST) -> bool:
    if not isinstance(node, ast.Call):
        return False
    func = node.func
    return (
        isinstance(func, ast.Attribute)
        and func.attr in {"append", "extend"}
        and isinstance(func.value, ast.Name)
        and "error" in func.value.id.lower()
    )


def _literal_arg_texts(arg: ast.AST) -> list[str]:
    text = _literal_text(arg)
    if text is not None:
        return [text]
    if isinstance(arg, ast.List | ast.Tuple):
        return [t for item in arg.elts if (t := _literal_text(item)) is not None]
    return []


def module_has_unactionable_error(path: Path, *, markers: tuple[str, ...]) -> bool:
    """True iff ``path`` appends an error string lacking any action ``markers``.

    Pure helper (the detection core) so tests can assert on it directly. Walks
    ``<errors>.append/extend(...)`` calls and flags a literal-string argument
    carrying none of the (case-insensitive) markers. A syntax/decode error
    returns False (another check owns unparseable files).
    """
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (SyntaxError, UnicodeDecodeError):
        return False
    lowered = tuple(m.lower() for m in markers)
    for node in ast.walk(tree):
        if not _is_error_append_call(node):
            continue
        assert isinstance(node, ast.Call)  # noqa: S101  # narrowed by _is_error_append_call
        for arg in node.args:
            for text in _literal_arg_texts(arg):
                if not any(marker in text.lower() for marker in lowered):
                    return True
    return False


class ActionableFeedback(FitnessRule):
    """Flags check modules emitting errors without a fix/next/run marker."""

    name = "actionable-feedback"
    remediation = REMEDIATION
    extensions = (".py",)

    #: The action markers a message must carry. Instance attribute so
    #: ``from_config`` can override; class default is the affordance shape.
    markers: tuple[str, ...] = DEFAULT_ACTION_MARKERS

    @classmethod
    def from_config(
        cls,
        config: Mapping[str, Any],
        *,
        repo_root: Path | None = None,
    ) -> ActionableFeedback:
        rule = super().from_config(config, repo_root=repo_root)
        assert isinstance(rule, ActionableFeedback)  # noqa: S101  # narrowing for mypy
        markers = config.get("markers")
        if markers is not None:
            rule.markers = tuple(markers)
        return rule

    def file_has_violation(self, path: Path) -> bool:
        return module_has_unactionable_error(path, markers=self.markers)


def build(
    config: Mapping[str, Any],
    *,
    repo_root: Path | None = None,
) -> ActionableFeedback:
    """Factory the engine calls to bind this CORE check to a consumer's config."""
    return ActionableFeedback.from_config(config, repo_root=repo_root)


def main(argv: list[str] | None = None) -> int:
    """CLI entry — supports ``--establish-baseline`` and ``--repo-root``."""
    return run_core_check(ActionableFeedback, argv)


if __name__ == "__main__":
    import sys

    sys.exit(main())
