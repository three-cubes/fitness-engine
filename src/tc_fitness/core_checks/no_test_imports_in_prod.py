"""CORE check: no-test-imports-in-prod — production never imports the test tree.

Production code MUST NOT import from the test package (``from tests.x import``
/ ``import tests``). The test tree is not shipped in the published wheel, so
any such import works in a local checkout (where the repo root is on
``sys.path`` via the test runner) but raises ``ModuleNotFoundError`` the
moment an end user installs the package and runs it.

Detection (AST over each in-scope production file):

* ``ast.ImportFrom`` whose ``module`` is a forbidden root or a dotted child.
* ``ast.Import`` where any alias name is a forbidden root or a dotted child.

The legitimate way to share a fake-like default is to ship it in the
production package itself (e.g. an ``InMemoryX`` / ``NullX``); the test tree
is for tests only -- by convention and by what the wheel actually ships.

Ported from kairix ``scripts/checks/check_no_test_imports_in_prod.py`` (F24)
and re-expressed as a configurable, repo-agnostic rule: the production scan
roots and the forbidden import roots arrive from config -- NO repo package
name is baked in. The forbidden root defaults to ``tests`` (the universal
test-tree convention), overridable per consumer.
"""

from __future__ import annotations

import ast
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from tc_fitness.core_checks import run_core_check
from tc_fitness.fitness_rule import FitnessRule
from tc_fitness.lib import remediation as _remediation

#: The conventional test-tree package name -- not repo identity. Overridable.
DEFAULT_FORBIDDEN_IMPORT_ROOTS: tuple[str, ...] = ("tests",)

REMEDIATION = _remediation(
    fix=(
        "move the symbol you needed out of the test tree and into the shipped "
        "package (e.g. as a NullX / InMemoryX in the relevant domain module) "
        "so it is part of the wheel. If the import is for a test seam, the "
        "production code should not carry that seam at all -- inject via a "
        "constructor argument and let the test pass the fake explicitly."
    ),
    nxt="re-run this check, then install the wheel and import the module to "
    "confirm it works with no test tree on sys.path.",
    run="python -m tc_fitness.core_checks.no_test_imports_in_prod",
    passing="from myapp.core.vector.null import NullVectorRepository",
    forbidden="from tests.fakes import FakeVectorRepository  # test tree not in wheel",
)


def _name_is_forbidden(name: str | None, roots: tuple[str, ...]) -> bool:
    """True if ``name`` is a forbidden root or any dotted child of one."""
    if name is None:
        return False
    return any(name == root or name.startswith(f"{root}.") for root in roots)


def file_imports_test_tree(path: Path, *, forbidden_roots: tuple[str, ...]) -> bool:
    """True iff ``path`` imports from a forbidden (test-tree) package.

    Pure helper (the detection core). A syntax / decode error is treated as
    "no violation" (another check owns unparseable files).
    """
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (SyntaxError, UnicodeDecodeError, OSError):
        return False
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and _name_is_forbidden(node.module, forbidden_roots):
            return True
        if isinstance(node, ast.Import):
            for alias in node.names:
                if _name_is_forbidden(alias.name, forbidden_roots):
                    return True
    return False


class NoTestImportsInProd(FitnessRule):
    """Flags production files that import from the test tree."""

    name = "no-test-imports-in-prod"
    remediation = REMEDIATION
    extensions = (".py",)

    #: The test-tree package roots production must never import.
    forbidden_import_roots: tuple[str, ...] = DEFAULT_FORBIDDEN_IMPORT_ROOTS

    @classmethod
    def from_config(
        cls,
        config: Mapping[str, Any],
        *,
        repo_root: Path | None = None,
    ) -> NoTestImportsInProd:
        rule = super().from_config(config, repo_root=repo_root)
        assert isinstance(rule, NoTestImportsInProd)  # noqa: S101  # narrowing for mypy
        roots = config.get("forbidden_import_roots")
        rule.forbidden_import_roots = tuple(roots) if roots is not None else DEFAULT_FORBIDDEN_IMPORT_ROOTS
        return rule

    def file_has_violation(self, path: Path) -> bool:
        return file_imports_test_tree(path, forbidden_roots=self.forbidden_import_roots)


def build(config: Mapping[str, Any], *, repo_root: Path | None = None) -> NoTestImportsInProd:
    """Factory the engine calls to bind this CORE check to a consumer's config."""
    return NoTestImportsInProd.from_config(config, repo_root=repo_root)


def main(argv: list[str] | None = None) -> int:
    """CLI entry — supports ``--establish-baseline`` and ``--repo-root``."""
    return run_core_check(NoTestImportsInProd, argv)


if __name__ == "__main__":
    import sys

    sys.exit(main())
