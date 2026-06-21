"""CORE check: no_language_suffix_in_package_names — names describe work, not tech.

A public package boundary (an MCP server, a shared library, a plugin, a service,
a published skill, a workspace package) must carry a name that describes the WORK
it performs. A language suffix (``-py``, ``-ts``, ``-js``, ``-rs``, …) leaks the
implementation choice into the agent-facing surface and couples the name to a
tech decision that may later change. This rule flags any boundary directory whose
immediate name ends in a forbidden language suffix.

The check inspects directory NAMES, never file content. It enumerates the
immediate child directories of each configured boundary root (and, for
marker-gated roots, only leaves carrying a marker file such as ``SKILL.md``) and
flags those ending in a forbidden suffix.

Ported from tc-agent-zone ``scripts/checks/no_language_suffix_in_package_names.py``
(ADR-029 D1+D5) and re-expressed as a configurable, repo-agnostic rule. The donor
hardcoded ``agentic/tools/mcp`` etc. and the SKILL.md marker; here:

* ``boundary_roots`` — prefixes whose immediate child dirs are boundaries.
* ``marker_roots`` — prefixes scanned at depth-2, gating each leaf on a marker.
* ``marker_file`` — the file a marker-root leaf must contain to count.
* ``forbidden_suffixes`` — the language suffixes to ban.

A consumer with no roots configured flags nothing.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from tc_fitness.core_checks import run_core_check
from tc_fitness.fitness_rule import FitnessRule
from tc_fitness.lib import remediation as _remediation

#: Language suffixes that leak implementation into a name. Domain-intrinsic
#: default (ADR-029's own list), overridable via config.
DEFAULT_FORBIDDEN_SUFFIXES: tuple[str, ...] = (
    "-py",
    "-ts",
    "-js",
    "-rs",
    "-go",
    "-cpp",
    "-c",
    "-java",
    "-kt",
)

#: The file a marker-root leaf must carry to count as a published boundary.
DEFAULT_MARKER_FILE = "SKILL.md"

REMEDIATION = _remediation(
    fix=(
        "rename the directory so the name describes the work, not the language "
        "(move the language to a sub-path or drop it). Update every import, "
        "package manifest and workspace member that referenced the old name."
    ),
    nxt="re-run this check to confirm the rename cleared the suffix.",
    run="python -m tc_fitness.core_checks.no_language_suffix_in_package_names",
    passing="render-office-to-pdf/",
    forbidden="render-office-to-pdf-ts/",
)


def _match_suffix(name: str, suffixes: tuple[str, ...]) -> str | None:
    """Return the forbidden suffix ``name`` ends with, or None."""
    for suffix in suffixes:
        if name.endswith(suffix):
            return suffix
    return None


def name_has_language_suffix(name: str, *, suffixes: tuple[str, ...]) -> bool:
    """True iff directory ``name`` ends with a forbidden language suffix.

    Pure helper (the detection core) so tests can assert on it directly.
    """
    return _match_suffix(name, suffixes) is not None


class NoLanguageSuffixInPackageNames(FitnessRule):
    """Flags boundary directories whose name ends in a language suffix."""

    name = "no-language-suffix-in-package-names"
    remediation = REMEDIATION
    #: This rule scans directory names; the extension filter is unused (its
    #: scope is the boundary roots), so it accepts anything.
    extensions = ()

    boundary_roots: tuple[str, ...] = ()
    marker_roots: tuple[str, ...] = ()
    marker_file: str = DEFAULT_MARKER_FILE
    forbidden_suffixes: tuple[str, ...] = DEFAULT_FORBIDDEN_SUFFIXES

    @classmethod
    def from_config(
        cls,
        config: Mapping[str, Any],
        *,
        repo_root: Path | None = None,
    ) -> NoLanguageSuffixInPackageNames:
        rule = super().from_config(config, repo_root=repo_root)
        assert isinstance(rule, NoLanguageSuffixInPackageNames)  # noqa: S101  # narrowing for mypy
        boundary = config.get("boundary_roots")
        if boundary is not None:
            rule.boundary_roots = tuple(boundary)
        markers = config.get("marker_roots")
        if markers is not None:
            rule.marker_roots = tuple(markers)
        marker_file = config.get("marker_file")
        if marker_file is not None:
            rule.marker_file = str(marker_file)
        suffixes = config.get("forbidden_suffixes")
        if suffixes is not None:
            rule.forbidden_suffixes = tuple(suffixes)
        return rule

    def _visible_child_dirs(self, parent: Path) -> list[Path]:
        if not parent.is_dir():
            return []
        return [c for c in sorted(parent.iterdir()) if c.is_dir() and not c.name.startswith(".")]

    def is_in_scope(self, rel: str) -> bool:
        # Scope is the enumerated boundary directories themselves; every
        # enumerated entry is in scope.
        return True

    def enumerate_files(self) -> list[Path]:
        """Enumerate the boundary directories (depth-1 + marker-gated depth-2)."""
        out: list[Path] = []
        for boundary in self.boundary_roots:
            out.extend(self._visible_child_dirs(self._repo_root / boundary))
        for marker_root in self.marker_roots:
            for scope in self._visible_child_dirs(self._repo_root / marker_root):
                for leaf in self._visible_child_dirs(scope):
                    if (leaf / self.marker_file).is_file():
                        out.append(leaf)
        return out

    def file_has_violation(self, path: Path) -> bool:
        return name_has_language_suffix(path.name, suffixes=self.forbidden_suffixes)


def build(
    config: Mapping[str, Any],
    *,
    repo_root: Path | None = None,
) -> NoLanguageSuffixInPackageNames:
    """Factory the engine calls to bind this CORE check to a consumer's config."""
    return NoLanguageSuffixInPackageNames.from_config(config, repo_root=repo_root)


def main(argv: list[str] | None = None) -> int:
    """CLI entry — supports ``--establish-baseline`` and ``--repo-root``."""
    return run_core_check(NoLanguageSuffixInPackageNames, argv)


if __name__ == "__main__":
    import sys

    sys.exit(main())
