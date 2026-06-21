"""CORE check: script-help-smoke — every argparse CLI answers ``--help``.

A single smoke that proves an argparse CLI is invocable AND wires every
declared long-flag through to its ``--help`` output -- catching the whole
"the script doesn't actually run / the parser fails to build" class of
regression with far less surface than enumerating every flag/state cell.

Contract: for every in-scope Python file that (1) defines a ``main()``
function AND (2) instantiates ``argparse.ArgumentParser(...)``, this gate
asserts both:

  - ``python <script> --help`` exits 0 within the configured timeout, AND
  - every long-form flag declared via ``add_argument("--name", ...)`` appears
    in the captured ``--help`` output.

A file that times out (work runs at import time before argparse fires) or
exits non-zero, or whose help omits a declared flag, is a violation.

Ported from tc-agent-zone ``scripts/checks/script_help_smoke.py`` and
re-expressed as a configurable, repo-agnostic rule: scan roots, the skipped
directory segments, the per-file exemptions, the help timeout, and the Python
interpreter all arrive from config -- NO repo path is baked in. The only
intrinsic constant is the ``--help`` invocation contract itself.
"""

from __future__ import annotations

import ast
import subprocess
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from tc_fitness.core_checks import run_core_check
from tc_fitness.fitness_rule import FitnessRule
from tc_fitness.lib import remediation as _remediation

#: Default --help timeout. A CLI that cannot print help in this window is
#: broken for the smoke contract (it likely runs work at import time).
DEFAULT_HELP_TIMEOUT_SECONDS = 5
#: Directory segments never treated as a public CLI surface.
DEFAULT_SKIP_DIR_SEGMENTS: tuple[str, ...] = ("__pycache__", ".venv", "node_modules", "tests", "test")

REMEDIATION = _remediation(
    fix=(
        "make `python <script> --help` exit 0 and list every declared --flag. "
        "argparse renders all add_argument flags by default, so a missing flag "
        "means the parser fails to build or the script does work at import time "
        "before argparse fires -- move that work inside main()."
    ),
    nxt="re-run this check to confirm it goes green.",
    run="python -m tc_fitness.core_checks.script_help_smoke",
    passing="python scripts/my_cli.py --help  # exits 0, lists --agent --out-dir",
    forbidden="a CLI that imports a heavy dep at module top-level so --help times out",
)


def _is_argument_parser_call(node: ast.AST) -> bool:
    if not isinstance(node, ast.Call):
        return False
    func = node.func
    name_match = isinstance(func, ast.Name) and func.id == "ArgumentParser"
    attr_match = isinstance(func, ast.Attribute) and func.attr == "ArgumentParser"
    return name_match or attr_match


def _has_main_and_argparse(tree: ast.AST) -> bool:
    """True iff the module declares ``def main()`` AND instantiates ``ArgumentParser``."""
    has_main = False
    has_argparse = False
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "main":
            has_main = True
        elif _is_argument_parser_call(node):
            has_argparse = True
        if has_main and has_argparse:
            return True
    return False


def _is_add_argument_call(node: ast.AST) -> bool:
    if not isinstance(node, ast.Call):
        return False
    func = node.func
    return isinstance(func, ast.Attribute) and func.attr == "add_argument"


def _long_flag_arg(arg: ast.expr) -> str | None:
    if not (isinstance(arg, ast.Constant) and isinstance(arg.value, str)):
        return None
    return arg.value if arg.value.startswith("--") else None


def extract_declared_flags(tree: ast.AST) -> tuple[str, ...]:
    """Return every long-form flag declared via ``add_argument("--name", ...)``.

    Pure helper (the detection core): positional args and short flags are
    intentionally ignored -- the smoke contract is about long-form flags being
    wired through to the help surface.
    """
    flags: list[str] = []
    seen: set[str] = set()
    for node in ast.walk(tree):
        if not _is_add_argument_call(node):
            continue
        for arg in node.args:  # type: ignore[attr-defined]  # narrowed by _is_add_argument_call
            name = _long_flag_arg(arg)
            if name and name not in seen:
                seen.add(name)
                flags.append(name)
    return tuple(flags)


def _parse(path: Path) -> ast.AST | None:
    try:
        return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (OSError, SyntaxError):
        return None


def _run_help(script: Path, *, python: str, timeout: int) -> tuple[int | None, str]:
    """Invoke ``<python> <script> --help`` with a hard timeout.

    Returns (exit_code, combined_output); ``exit_code is None`` on timeout.
    """
    try:
        result = subprocess.run(  # noqa: S603  # args are repo-internal script paths
            [python, str(script), "--help"],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return None, ""
    return result.returncode, (result.stdout or "") + (result.stderr or "")


def script_help_violates(script: Path, *, python: str, timeout: int) -> bool:
    """True iff ``--help`` times out, exits non-zero, or omits a declared flag.

    Pure helper (the detection core) so tests can drive it against a fixture
    script without going through the gate machinery.
    """
    tree = _parse(script)
    if tree is None or not _has_main_and_argparse(tree):
        return False
    declared = extract_declared_flags(tree)
    code, output = _run_help(script, python=python, timeout=timeout)
    if code is None or code != 0:
        return True
    return any(flag not in output for flag in declared)


class ScriptHelpSmoke(FitnessRule):
    """Flags argparse CLIs whose ``--help`` is broken or omits a declared flag."""

    name = "script-help-smoke"
    remediation = REMEDIATION
    extensions = (".py",)

    #: Rule-specific knobs.
    skip_dir_segments: tuple[str, ...] = DEFAULT_SKIP_DIR_SEGMENTS
    help_timeout_seconds: int = DEFAULT_HELP_TIMEOUT_SECONDS
    python_executable: str = sys.executable

    @classmethod
    def from_config(
        cls,
        config: Mapping[str, Any],
        *,
        repo_root: Path | None = None,
    ) -> ScriptHelpSmoke:
        rule = super().from_config(config, repo_root=repo_root)
        assert isinstance(rule, ScriptHelpSmoke)  # noqa: S101  # narrowing for mypy
        skips = config.get("skip_dir_segments")
        rule.skip_dir_segments = tuple(skips) if skips is not None else DEFAULT_SKIP_DIR_SEGMENTS
        rule.help_timeout_seconds = int(config.get("help_timeout_seconds", DEFAULT_HELP_TIMEOUT_SECONDS))
        rule.python_executable = config.get("python_executable") or sys.executable
        return rule

    def is_in_scope(self, rel: str) -> bool:
        if not super().is_in_scope(rel):
            return False
        parts = Path(rel).parts
        if any(seg in parts for seg in self.skip_dir_segments):
            return False
        return not Path(rel).name.startswith("test_")

    def file_has_violation(self, path: Path) -> bool:
        return script_help_violates(
            path,
            python=self.python_executable,
            timeout=self.help_timeout_seconds,
        )


def build(config: Mapping[str, Any], *, repo_root: Path | None = None) -> ScriptHelpSmoke:
    """Factory the engine calls to bind this CORE check to a consumer's config."""
    return ScriptHelpSmoke.from_config(config, repo_root=repo_root)


def main(argv: list[str] | None = None) -> int:
    """CLI entry — supports ``--establish-baseline`` and ``--repo-root``."""
    return run_core_check(ScriptHelpSmoke, argv)


if __name__ == "__main__":
    import sys as _sys

    _sys.exit(main())
