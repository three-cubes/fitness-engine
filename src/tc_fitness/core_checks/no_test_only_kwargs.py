"""CORE check: no-test-only-kwargs — forbid ``*_fn=None`` test seams in prod.

A production free function declaring a parameter whose name ends in a
test-seam suffix (``_fn`` / ``_loader`` / ``_factory`` ...) defaulting to
``None`` is the canonical "I added a test seam to production" smell:
production grows complexity for tests without operator value, the swap-point
sits on every production call, and nothing forces tests to actually use the
seam (so it rots). The legitimate substitution pattern is constructor
injection on a ``Deps`` dataclass at a boundary class.

Detection (AST, free functions only -- methods on a ``ClassDef`` are exempt
because they ARE the canonical Deps-constructor shape): any
positional-with-default or keyword-only parameter whose name ends in a
configured seam suffix AND whose default is the ``None`` constant.

A consumer documents legitimate seams (a real production caller passes a
non-default, or a Protocol/Adapter wiring point at a true boundary) via the
``exempt_keys`` config -- one entry per allowed seam in the format
``<rel-path>::<function-name>::<param-name>``.

Ported from tc-agent-zone ``scripts/checks/no_test_only_kwargs.py`` (itself
kairix F6) and re-expressed as a configurable, repo-agnostic rule: scan roots
and the per-seam allow-list arrive from config; the seam suffixes are the
rule's own shape (overridable).
"""

from __future__ import annotations

import ast
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from tc_fitness.core_checks import run_core_check
from tc_fitness.fitness_rule import FitnessRule
from tc_fitness.lib import remediation as _remediation

#: Default test-seam parameter-name suffixes -- the smell's own shape, not
#: repo identity. Overridable via config.
DEFAULT_SEAM_SUFFIXES: tuple[str, ...] = ("_fn",)

REMEDIATION = _remediation(
    fix=(
        "delete the *_fn=None parameter and move the collaborator onto a "
        "@dataclass Deps class with field(default_factory=...); tests "
        "construct an overridden Deps and pass it as a single argument. If a "
        "flagged parameter is a genuine boundary wiring point, document it in "
        "the exempt_keys config as <rel-path>::<function-name>::<param-name>."
    ),
    nxt="re-run this check to confirm it goes green.",
    run="python -m tc_fitness.core_checks.no_test_only_kwargs",
    passing="def route(intent: str, deps: RouterDeps | None = None) -> str: ...",
    forbidden="def route(intent: str, clock_fn=None, load_routes_fn=None) -> str: ...",
)


def _is_test_only_kwarg(param: ast.arg | None, default: ast.expr | None, suffixes: tuple[str, ...]) -> bool:
    """True iff (param, default) describes a ``*<suffix>=None`` kwarg."""
    if param is None or default is None:
        return False
    if not param.arg.endswith(suffixes):
        return False
    return isinstance(default, ast.Constant) and default.value is None


def _scan_function(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
    suffixes: tuple[str, ...],
) -> list[tuple[str, str, int]]:
    """Return the test-only kwargs declared on a single function."""
    args = node.args
    defaults_for_args = dict(zip(reversed(args.args), reversed(args.defaults), strict=False))
    kwonly_defaults = dict(zip(args.kwonlyargs, args.kw_defaults, strict=False))
    return [
        (node.name, param.arg, node.lineno)
        for param, default in {**defaults_for_args, **kwonly_defaults}.items()
        if _is_test_only_kwarg(param, default, suffixes)
    ]


def find_test_only_kwargs_in_file(path: Path, *, suffixes: tuple[str, ...]) -> list[tuple[str, str, int]]:
    """Return (function-name, param-name, lineno) for every seam parameter.

    Pure helper (the detection core): walks every free function (methods on a
    ``ClassDef`` are out of scope -- they are the canonical Deps shape). A
    syntax / decode error is treated as "no violation".
    """
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (SyntaxError, UnicodeDecodeError, OSError):
        return []
    class_func_ids: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            for child in ast.walk(node):
                if isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef):
                    class_func_ids.add(id(child))
    out: list[tuple[str, str, int]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and id(node) not in class_func_ids:
            out.extend(_scan_function(node, suffixes))
    return out


class NoTestOnlyKwargs(FitnessRule):
    """Flags production free functions with ``*_fn=None`` test seams."""

    name = "no-test-only-kwargs"
    remediation = REMEDIATION
    extensions = (".py",)

    #: Rule-specific knobs.
    seam_suffixes: tuple[str, ...] = DEFAULT_SEAM_SUFFIXES
    #: Allowed seams: ``<rel-path>::<function-name>::<param-name>`` entries.
    exempt_keys: frozenset[str] = frozenset()

    @classmethod
    def from_config(
        cls,
        config: Mapping[str, Any],
        *,
        repo_root: Path | None = None,
    ) -> NoTestOnlyKwargs:
        rule = super().from_config(config, repo_root=repo_root)
        assert isinstance(rule, NoTestOnlyKwargs)  # noqa: S101  # narrowing for mypy
        suffixes = config.get("seam_suffixes")
        rule.seam_suffixes = tuple(suffixes) if suffixes is not None else DEFAULT_SEAM_SUFFIXES
        keys = config.get("exempt_keys")
        rule.exempt_keys = frozenset(keys) if keys is not None else frozenset()
        return rule

    def file_has_violation(self, path: Path) -> bool:
        rel = self._repo_relative(path).as_posix()
        for func_name, param, _lineno in find_test_only_kwargs_in_file(path, suffixes=self.seam_suffixes):
            if f"{rel}::{func_name}::{param}" in self.exempt_keys:
                continue
            return True
        return False


def build(config: Mapping[str, Any], *, repo_root: Path | None = None) -> NoTestOnlyKwargs:
    """Factory the engine calls to bind this CORE check to a consumer's config."""
    return NoTestOnlyKwargs.from_config(config, repo_root=repo_root)


def main(argv: list[str] | None = None) -> int:
    """CLI entry — supports ``--establish-baseline`` and ``--repo-root``."""
    return run_core_check(NoTestOnlyKwargs, argv)


if __name__ == "__main__":
    import sys

    sys.exit(main())
