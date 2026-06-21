"""CORE check: test-skip-rationale — every skip mechanism carries a reason.

A silently-skipping test is a worse signal than a missing test: it looks
present, but never runs. This rule walks each in-scope test module via the
AST and flags any skip mechanism that lacks a documented rationale:

* ``@pytest.mark.skip`` / ``skipif`` / ``xfail`` MUST take a non-empty
  ``reason=`` kwarg (the bare attribute form fails).
* ``pytest.importorskip("X")`` MUST carry a ``reason=`` kwarg, an immediately
  preceding ``#`` comment block (within ``IMPORTORSKIP_COMMENT_LOOKBACK``
  lines, no blank gap), OR a same-line trailing ``#`` comment.
* A bare ``pytestmark = pytest.mark.skip`` module-level assignment fails too.

Ported from tc-agent-zone ``scripts/checks/test_skip_rationale.py`` (itself
kairix F11) and re-expressed as a configurable, repo-agnostic rule: the scan
roots (typically ``tests``) and file extensions arrive from the consumer's
``[tool.tc_fitness]`` config — NO repo paths are baked in. The only intrinsic
constant is the import-or-skip comment look-back window, which is the rule's
own shape and overridable.
"""

from __future__ import annotations

import ast
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from tc_fitness.core_checks import run_core_check
from tc_fitness.fitness_rule import FitnessRule
from tc_fitness.lib import remediation as _remediation

#: How many lines above an ``importorskip`` call to scan for a ``#`` comment.
#: The rule's own shape (not repo identity) — overridable via config.
DEFAULT_IMPORTORSKIP_COMMENT_LOOKBACK = 3

#: The skip decorators that require a ``reason=`` kwarg.
_REASON_REQUIRED_MARKS = ("skip", "skipif", "xfail")

REMEDIATION = _remediation(
    fix=(
        'add a non-empty reason="<why this skip is correct>" kwarg to every '
        "skip / skipif / xfail decorator, and either a reason= kwarg or an "
        "immediately-preceding # comment to every importorskip call. If the "
        "test is genuinely broken, delete the skip and fix the underlying "
        "issue instead -- a silent skip looks present but never runs."
    ),
    nxt="re-run this check to confirm it goes green.",
    run="python -m tc_fitness.core_checks.test_skip_rationale",
    passing='@pytest.mark.skip(reason="re-enabled once the upstream fix lands")',
    forbidden="@pytest.mark.skip  # bare -- no reason, silently never runs",
)


def _is_pytest_mark(decorator: ast.expr, mark_name: str) -> ast.expr | None:
    """Return ``decorator`` if it is a ``pytest.mark.<mark_name>`` reference."""
    target = decorator.func if isinstance(decorator, ast.Call) else decorator
    if isinstance(target, ast.Attribute) and target.attr == mark_name:
        inner = target.value
        if isinstance(inner, ast.Attribute) and inner.attr == "mark":
            return decorator
        if isinstance(inner, ast.Name) and inner.id == "mark":
            return decorator
    return None


def _has_reason_kwarg(call: ast.Call) -> bool:
    return any(
        kw.arg == "reason"
        and isinstance(kw.value, ast.Constant)
        and isinstance(kw.value.value, str)
        and kw.value.value.strip()
        for kw in call.keywords
    )


def _decorator_violates(decorator: ast.expr) -> bool:
    for mark_name in _REASON_REQUIRED_MARKS:
        match = _is_pytest_mark(decorator, mark_name)
        if match is None:
            continue
        if not isinstance(match, ast.Call):
            return True
        if not _has_reason_kwarg(match):
            return True
    return False


def _is_importorskip(node: ast.expr) -> ast.Call | None:
    if not isinstance(node, ast.Call):
        return None
    target = node.func
    if isinstance(target, ast.Attribute) and target.attr == "importorskip":
        inner = target.value
        if isinstance(inner, ast.Name) and inner.id == "pytest":
            return node
    return None


def _importorskip_has_rationale(call: ast.Call, source_lines: list[str], lookback: int) -> bool:
    if _has_reason_kwarg(call):
        return True
    line_idx = call.lineno - 1
    if 0 <= line_idx < len(source_lines):
        line = source_lines[line_idx]
        if "#" in line:
            after_hash = line.split("#", 1)[1].strip()
            if after_hash:
                return True
    for offset in range(1, lookback + 1):
        prev_idx = line_idx - offset
        if prev_idx < 0:
            return False
        stripped = source_lines[prev_idx].strip()
        if stripped == "":
            return False
        if stripped.startswith("#"):
            return True
    return False


def _decorator_violations(node: ast.AST) -> list[int]:
    if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
        return []
    return [d.lineno for d in node.decorator_list if _decorator_violates(d)]


def _pytestmark_candidates(value: ast.expr) -> list[ast.expr]:
    if isinstance(value, ast.List | ast.Tuple):
        return list(value.elts)
    return [value]


def _pytestmark_violations(node: ast.AST) -> list[int]:
    if not isinstance(node, ast.Assign):
        return []
    out: list[int] = []
    for target in node.targets:
        if not (isinstance(target, ast.Name) and target.id == "pytestmark"):
            continue
        for candidate in _pytestmark_candidates(node.value):
            if _decorator_violates(candidate):
                out.append(candidate.lineno)
    return out


def _importorskip_call(node: ast.AST) -> ast.Call | None:
    if isinstance(node, ast.Expr):
        return _is_importorskip(node.value)
    if isinstance(node, ast.Assign):
        return _is_importorskip(node.value)
    return None


def file_has_skip_without_reason(path: Path, *, importorskip_lookback: int) -> bool:
    """True iff ``path`` holds any skip mechanism that lacks a rationale.

    Pure helper (the detection core) so tests assert on it directly: parses
    the module, then checks every skip/skipif/xfail decorator, every bare
    ``pytestmark`` assignment, and every ``importorskip`` call. A syntax /
    decode error is treated as "no violation" (another check owns unparseable
    files).
    """
    try:
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(path))
    except (SyntaxError, UnicodeDecodeError, OSError):
        return False
    source_lines = source.splitlines()
    for node in ast.walk(tree):
        if _decorator_violations(node):
            return True
        if _pytestmark_violations(node):
            return True
        call = _importorskip_call(node)
        if call is not None and not _importorskip_has_rationale(call, source_lines, importorskip_lookback):
            return True
    return False


class TestSkipRationale(FitnessRule):
    """Flags test files with an undocumented skip/skipif/xfail/importorskip."""

    #: Tell pytest this is a rule class, not a test class (the ``Test`` prefix
    #: would otherwise trigger collection).
    __test__ = False

    name = "test-skip-rationale"
    remediation = REMEDIATION
    extensions = (".py",)

    #: Rule-specific knob — the importorskip comment look-back window.
    importorskip_lookback: int = DEFAULT_IMPORTORSKIP_COMMENT_LOOKBACK

    @classmethod
    def from_config(
        cls,
        config: Mapping[str, Any],
        *,
        repo_root: Path | None = None,
    ) -> TestSkipRationale:
        rule = super().from_config(config, repo_root=repo_root)
        assert isinstance(rule, TestSkipRationale)  # noqa: S101  # narrowing for mypy
        rule.importorskip_lookback = int(
            config.get("importorskip_lookback", DEFAULT_IMPORTORSKIP_COMMENT_LOOKBACK)
        )
        return rule

    def file_has_violation(self, path: Path) -> bool:
        return file_has_skip_without_reason(path, importorskip_lookback=self.importorskip_lookback)


def build(config: Mapping[str, Any], *, repo_root: Path | None = None) -> TestSkipRationale:
    """Factory the engine calls to bind this CORE check to a consumer's config."""
    return TestSkipRationale.from_config(config, repo_root=repo_root)


def main(argv: list[str] | None = None) -> int:
    """CLI entry — supports ``--establish-baseline`` and ``--repo-root``."""
    return run_core_check(TestSkipRationale, argv)


if __name__ == "__main__":
    import sys

    sys.exit(main())
