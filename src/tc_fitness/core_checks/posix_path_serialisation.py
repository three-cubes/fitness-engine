"""CORE check: posix_path_serialisation — repo-relative paths serialised as POSIX.

A repo-relative path stringified with ``str(path.relative_to(root))`` carries the
HOST-native separator: backslashes on Windows, forward slashes on Linux. A
generated/consumed artefact committed cross-platform and read on Linux CI then
ships ``a\\b\\c`` where the consumer expects ``a/b/c`` — a bug invisible until CI
runs on the other OS. This rule bans the OS-native ``str(...)`` idiom; the single
sanctioned form is ``.relative_to(root).as_posix()``.

Detection is AST-based: it flags any ``str(...)`` call whose argument subtree
contains a ``.relative_to(...)`` call that is NOT immediately ``.as_posix()``-
terminated. The compliant forms (``p.relative_to(r).as_posix()`` and the
redundant ``str(p.relative_to(r).as_posix())``) are not flagged.

Ported from tc-agent-zone ``scripts/checks/posix_path_serialisation.py`` and
re-expressed as a configurable, repo-agnostic rule: the scan roots, in-scope
extensions, and exempt path SEGMENTS all arrive from the consumer's
``[tool.tc_fitness]`` config — NO repo paths or globs are baked in. Domain-
intrinsic defaults (the ``.py`` extension, ``__pycache__``/``.venv`` cache
segments) are the rule's own shape, overridable via config.
"""

from __future__ import annotations

import ast
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from tc_fitness.core_checks import run_core_check
from tc_fitness.fitness_rule import FitnessRule
from tc_fitness.lib import remediation as _remediation

#: Path SEGMENTS that take a file out of scope by default — cache/build dirs and
#: test trees that legitimately stringify ephemeral OS paths. Domain-intrinsic,
#: NOT repo identity; a consumer narrows or widens this via config.
DEFAULT_EXCLUDED_SEGMENTS: tuple[str, ...] = (
    "tests",
    "test",
    "node_modules",
    ".venv",
    "__pycache__",
)

REMEDIATION = _remediation(
    fix=(
        "replace `str(path.relative_to(root))` with "
        "`path.relative_to(root).as_posix()` (or build the value from a "
        "PurePosixPath); bare str() emits OS-native separators that break "
        "Linux CI when serialised on Windows."
    ),
    nxt="re-run this check to confirm the offending line is gone.",
    run="python -m tc_fitness.core_checks.posix_path_serialisation",
    passing="rel = path.relative_to(root).as_posix()   # always forward-slash",
    forbidden="rel = str(path.relative_to(root))         # OS-native separators leak",
)


def _compliant_relative_to_nodes(tree: ast.AST) -> set[int]:
    """Object-ids of ``relative_to(...)`` calls immediately ``.as_posix()``'d.

    ``path.relative_to(root).as_posix()`` is the sanctioned form, so the inner
    ``relative_to`` call must never be treated as a violation even inside a
    ``str(...)`` (the redundant-but-compliant ``str(p.relative_to(r).as_posix())``).
    """
    compliant: set[int] = set()
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "as_posix"
            and isinstance(node.func.value, ast.Call)
            and isinstance(node.func.value.func, ast.Attribute)
            and node.func.value.func.attr == "relative_to"
        ):
            compliant.add(id(node.func.value))
    return compliant


def _has_uncompliant_relative_to(node: ast.AST, compliant: set[int]) -> bool:
    for child in ast.walk(node):
        if (
            isinstance(child, ast.Call)
            and isinstance(child.func, ast.Attribute)
            and child.func.attr == "relative_to"
            and id(child) not in compliant
        ):
            return True
    return False


def module_has_os_native_serialisation(path: Path) -> bool:
    """True iff ``path`` stringifies a non-``as_posix`` ``relative_to`` result.

    Pure helper (the detection core) so tests can assert on it directly. Flags a
    ``str(...)`` call whose argument subtree holds a ``relative_to(...)`` that is
    not ``.as_posix()``-terminated. A syntax/decode error returns False (another
    check owns unparseable files).
    """
    try:
        tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"), filename=str(path))
    except (SyntaxError, ValueError):
        return False
    compliant = _compliant_relative_to_nodes(tree)
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "str"
            and node.args
            and _has_uncompliant_relative_to(node.args[0], compliant)
        ):
            return True
    return False


class PosixPathSerialisation(FitnessRule):
    """Flags OS-native ``str(path.relative_to(root))`` serialisation."""

    name = "posix-path-serialisation"
    remediation = REMEDIATION
    extensions = (".py",)

    #: Path segments that exclude a file from scope. Instance attribute so
    #: ``from_config`` can override; class default is the rule's own shape.
    excluded_segments: tuple[str, ...] = DEFAULT_EXCLUDED_SEGMENTS

    @classmethod
    def from_config(
        cls,
        config: Mapping[str, Any],
        *,
        repo_root: Path | None = None,
    ) -> PosixPathSerialisation:
        rule = super().from_config(config, repo_root=repo_root)
        assert isinstance(rule, PosixPathSerialisation)  # noqa: S101  # narrowing for mypy
        segments = config.get("excluded_segments")
        if segments is not None:
            rule.excluded_segments = tuple(segments)
        return rule

    def is_in_scope(self, rel: str) -> bool:
        if any(seg in self.excluded_segments for seg in rel.split("/")):
            return False
        return super().is_in_scope(rel)

    def file_has_violation(self, path: Path) -> bool:
        return module_has_os_native_serialisation(path)


def build(
    config: Mapping[str, Any],
    *,
    repo_root: Path | None = None,
) -> PosixPathSerialisation:
    """Factory the engine calls to bind this CORE check to a consumer's config."""
    return PosixPathSerialisation.from_config(config, repo_root=repo_root)


def main(argv: list[str] | None = None) -> int:
    """CLI entry — supports ``--establish-baseline`` and ``--repo-root``."""
    return run_core_check(PosixPathSerialisation, argv)


if __name__ == "__main__":
    import sys

    sys.exit(main())
