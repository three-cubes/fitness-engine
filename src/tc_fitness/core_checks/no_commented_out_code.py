"""CORE check: no_commented_out_code — Sonar S125.

A run of consecutive ``#``-prefixed lines that lex as valid Python statements
is commented-out code. Git history is the archive — dead code in a comment
accumulates confusion (is it still relevant? the intended replacement for the
line below?). The fix is always to delete it; ``git log -p`` recovers any
prior state.

Ported from kairix ``scripts/checks/check_no_commented_out_code.py`` (F18) and
re-expressed as a configurable, repo-agnostic rule: the only domain-intrinsic
number is S125's own minimum-run length (3 contiguous comment lines), exposed
as a ``min_run`` knob the consumer overrides via ``[tool.tc_fitness]``. No repo
paths or globs are baked in.
"""

from __future__ import annotations

import ast
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from tc_fitness.core_checks import run_core_check
from tc_fitness.fitness_rule import FitnessRule
from tc_fitness.lib import remediation as _remediation

#: S125's own minimum run — domain-intrinsic, not repo identity. Overridable.
DEFAULT_MIN_RUN = 3

# Directive comments (shebangs, encoding cookies, tool directives) are never code.
_DIRECTIVE_RE = re.compile(r"^\s*#\s*(!|pyright:|type:\s*ignore|noqa|nosec|pragma:|coding[:=])")
# Section-rule boilerplate (``# -----`` / ``# =====`` / box-drawing rules) is never code.
_BOILERPLATE_RE = re.compile(r"^\s*#\s*[=─—―-]{3,}")

# Syntactic anchors: a comment block lacking all of these is prose, not code.
_CODE_ANCHORS = (
    "=",
    "(",
    "import ",
    "def ",
    "class ",
    "return",
    "raise",
    "if ",
    "for ",
    "while ",
    "with ",
)

REMEDIATION = _remediation(
    fix=(
        "delete the run of consecutive commented-out Python lines outright; "
        "if the code might come back, leave a referenced TODO with a ticket "
        "number instead of the dead code itself (git history is the archive)."
    ),
    nxt="re-run this check to confirm the gate goes green.",
    run="python -m tc_fitness.core_checks.no_commented_out_code",
    passing="# Strip leading slash so we can join cleanly.\\npath = path.lstrip('/')",
    forbidden="# old = path.replace('/a/','/b/')\\n# if old.startswith('/x'):\\n#     old = old[6:]",
)


def _strip_comment_prefix(line: str) -> str:
    """Strip leading whitespace + ``#`` + one optional space, preserving the rest."""
    leading_ws = len(line) - len(line.lstrip())
    rest = line[leading_ws:]
    if not rest.startswith("#"):
        return ""
    rest = rest[1:]
    if rest.startswith(" "):
        rest = rest[1:]
    return rest


def _is_commentlike_directive(line: str) -> bool:
    return bool(_DIRECTIVE_RE.match(line) or _BOILERPLATE_RE.match(line))


def _looks_like_code(block_text: str) -> bool:
    """True if the dedented comment block parses as one or more Python statements."""
    stripped = block_text.strip("\n")
    if not stripped:
        return False
    if not any(marker in stripped for marker in _CODE_ANCHORS):
        return False
    try:
        lines = stripped.splitlines()
        non_empty = [line for line in lines if line.strip()]
        if not non_empty:
            return False
        min_indent = min(len(line) - len(line.lstrip()) for line in non_empty)
        dedented = "\n".join(line[min_indent:] if line.strip() else "" for line in lines)
        ast.parse(dedented)
        return True
    except (SyntaxError, ValueError, IndentationError):
        return False


def _docstring_lines(tree: ast.AST) -> set[int]:
    out: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Module | ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef) and node.body:
            first = node.body[0]
            if (
                isinstance(first, ast.Expr)
                and isinstance(first.value, ast.Constant)
                and isinstance(first.value.value, str)
            ):
                start = first.lineno
                end = first.end_lineno or first.lineno
                out.update(range(start, end + 1))
    return out


def module_has_commented_code(path: Path, *, min_run: int) -> bool:
    """True iff ``path`` has a run of ``min_run``+ comment lines that lex as code.

    Pure helper (the detection core). Docstrings and directive/boilerplate
    comments are skipped. A syntax / decode error is treated as "no violation".
    """
    try:
        text = path.read_text(encoding="utf-8")
        tree = ast.parse(text, filename=str(path))
    except (SyntaxError, UnicodeDecodeError, OSError):
        return False

    docstring_lines = _docstring_lines(tree)
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        if i + 1 in docstring_lines:
            i += 1
            continue
        stripped = line.strip()
        if not stripped.startswith("#") or _is_commentlike_directive(line):
            i += 1
            continue
        block_lines: list[str] = []
        j = i
        while j < len(lines):
            cur = lines[j]
            if not cur.strip().startswith("#") or _is_commentlike_directive(cur):
                break
            if j + 1 in docstring_lines:
                break
            block_lines.append(_strip_comment_prefix(cur))
            j += 1
        if len(block_lines) >= min_run and _looks_like_code("\n".join(block_lines)):
            return True
        i = j + 1 if j == i else j
    return False


class NoCommentedOutCode(FitnessRule):
    """Flags files holding a run of commented-out Python code (Sonar S125)."""

    name = "no-commented-out-code"
    remediation = REMEDIATION
    extensions = (".py",)

    #: Rule-specific knob — S125's own minimum run; overridable per consumer.
    min_run: int = DEFAULT_MIN_RUN

    @classmethod
    def from_config(
        cls,
        config: Mapping[str, Any],
        *,
        repo_root: Path | None = None,
    ) -> NoCommentedOutCode:
        rule = super().from_config(config, repo_root=repo_root)
        assert isinstance(rule, NoCommentedOutCode)  # noqa: S101  # narrowing for mypy
        rule.min_run = int(config.get("min_run", DEFAULT_MIN_RUN))
        return rule

    def file_has_violation(self, path: Path) -> bool:
        return module_has_commented_code(path, min_run=self.min_run)


def build(config: Mapping[str, Any], *, repo_root: Path | None = None) -> NoCommentedOutCode:
    """Factory the engine calls to bind this CORE check to a consumer's config."""
    return NoCommentedOutCode.from_config(config, repo_root=repo_root)


def main(argv: list[str] | None = None) -> int:
    """CLI entry — supports ``--establish-baseline`` and ``--repo-root``."""
    return run_core_check(NoCommentedOutCode, argv)


if __name__ == "__main__":
    import sys

    sys.exit(main())
