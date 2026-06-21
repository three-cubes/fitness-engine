"""CORE check: no-env-monkeypatch — no env-var monkeypatch on owned keys.

A test that does ``monkeypatch.setenv("MYAPP_DATA_DIR", ...)`` to influence a
production env-var read couples the test to a global side-channel and hides
the boundary. The boundary-only pattern reads each owned env var ONCE inside a
``Paths``-style class; tests construct that object directly and inject it.

This rule walks each in-scope test file via the AST and flags any
``monkeypatch.{setenv,setattr,delenv}(...)`` call whose first positional
argument is a string literal beginning with one of the configured env-var
prefixes.

Ported from kairix ``scripts/checks/check_no_env_monkeypatch.py`` (F2) and
re-expressed as a configurable, repo-agnostic rule: the env-var prefixes that
identify an owned key arrive from config -- NO repo-specific prefix is baked
in. (A rule bound with no prefixes matches nothing.)
"""

from __future__ import annotations

import ast
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from tc_fitness.core_checks import run_core_check
from tc_fitness.fitness_rule import FitnessRule
from tc_fitness.lib import remediation as _remediation

#: The monkeypatch methods that mutate env / attribute state.
_TARGET_METHODS = frozenset({"setenv", "setattr", "delenv"})

REMEDIATION = _remediation(
    fix=(
        'replace monkeypatch.setenv("<PREFIX>_...", ...) with explicit '
        "construction of a Paths-style boundary object and pass it as an "
        "argument to the use case. If production reads the env var directly, "
        "refactor it to accept that boundary object as an explicit argument."
    ),
    nxt="re-run this check to confirm it goes green.",
    run="python -m tc_fitness.core_checks.no_env_monkeypatch",
    passing="paths = FakePaths(data_dir=tmp_path); result = use_case(paths=paths)",
    forbidden='monkeypatch.setenv("MYAPP_DATA_DIR", str(tmp_path)); result = use_case()',
)


def _is_monkeypatch_call(call: ast.Call) -> bool:
    """``monkeypatch.{setenv,setattr,delenv}(...)`` on the bare receiver name."""
    func = call.func
    if not isinstance(func, ast.Attribute):
        return False
    if func.attr not in _TARGET_METHODS:
        return False
    return isinstance(func.value, ast.Name) and func.value.id == "monkeypatch"


def _first_arg_has_prefix(call: ast.Call, prefixes: tuple[str, ...]) -> bool:
    """First positional arg is a string literal starting with a configured prefix."""
    if not call.args or not prefixes:
        return False
    first = call.args[0]
    if isinstance(first, ast.Constant) and isinstance(first.value, str):
        return first.value.startswith(prefixes)
    return False


def file_has_env_monkeypatch(path: Path, *, prefixes: tuple[str, ...]) -> bool:
    """True iff ``path`` monkeypatches an env/attr key matching a prefix.

    Pure helper (the detection core). A syntax / decode error is treated as
    "no violation" (another check owns unparseable files).
    """
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (SyntaxError, UnicodeDecodeError, OSError):
        return False
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and _is_monkeypatch_call(node)
            and _first_arg_has_prefix(node, prefixes)
        ):
            return True
    return False


class NoEnvMonkeypatch(FitnessRule):
    """Flags test files that monkeypatch an owned env-var key."""

    name = "no-env-monkeypatch"
    remediation = REMEDIATION
    extensions = (".py",)

    #: Owned env-var prefixes -- repo-supplied, no default identity.
    env_prefixes: tuple[str, ...] = ()

    @classmethod
    def from_config(
        cls,
        config: Mapping[str, Any],
        *,
        repo_root: Path | None = None,
    ) -> NoEnvMonkeypatch:
        rule = super().from_config(config, repo_root=repo_root)
        assert isinstance(rule, NoEnvMonkeypatch)  # noqa: S101  # narrowing for mypy
        prefixes = config.get("env_prefixes")
        rule.env_prefixes = tuple(prefixes) if prefixes is not None else ()
        return rule

    def file_has_violation(self, path: Path) -> bool:
        return file_has_env_monkeypatch(path, prefixes=self.env_prefixes)


def build(config: Mapping[str, Any], *, repo_root: Path | None = None) -> NoEnvMonkeypatch:
    """Factory the engine calls to bind this CORE check to a consumer's config."""
    return NoEnvMonkeypatch.from_config(config, repo_root=repo_root)


def main(argv: list[str] | None = None) -> int:
    """CLI entry — supports ``--establish-baseline`` and ``--repo-root``."""
    return run_core_check(NoEnvMonkeypatch, argv)


if __name__ == "__main__":
    import sys

    sys.exit(main())
