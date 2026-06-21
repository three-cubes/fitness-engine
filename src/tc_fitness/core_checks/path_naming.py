"""CORE check: path_naming — repo paths follow the configured naming convention.

Consistent path naming is a discoverability contract: a reader who knows the
convention can predict where a file lives and what it is called. This rule flags
a path whose NAME violates the convention configured for the root it sits under —
``kebab-case`` for docs/dirs, ``snake_case`` for importable Python — while a
ratcheted baseline grandfathers pre-existing offenders so adoption never breaks.

The check inspects path NAMES, never file content. It enumerates every in-scope
path and flags the ones whose stem/name fails the convention regex for its root,
unless the name is on the always-allowed list (``README.md``, ``LICENSE``, …) or
the path matches an exempt-segment.

Ported from tc-agent-zone ``scripts/checks/path_naming.py`` and re-expressed as a
configurable, repo-agnostic rule. The donor hardcoded ``docs/``, ``scripts/checks``,
the ADR pattern and a fixed allow-list; here EVERY convention is config:

* ``kebab_roots`` — repo-relative prefixes whose ``.md`` files must be kebab-case.
* ``snake_roots`` — repo-relative prefixes whose ``.py`` files must be snake_case
  (an importable module name).
* ``allowed_names`` — exact filenames exempt from any convention.
* ``exempt_segments`` — path segments (cache/build dirs) that drop a path.

A consumer with no roots configured flags nothing.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from tc_fitness.core_checks import run_core_check
from tc_fitness.fitness_rule import FitnessRule
from tc_fitness.lib import remediation as _remediation

#: A kebab-case stem: lowercase, digits, hyphens; no leading hyphen.
KEBAB_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")
#: An importable Python module name: ``__init__`` or an optionally-underscored
#: lowercase snake stem with a ``.py`` suffix.
SNAKE_RE = re.compile(r"^(__init__|_?[a-z0-9][a-z0-9_]*)\.py$")

#: Filenames that are conventionally upper-case / fixed and exempt from the
#: kebab/snake rules. Domain-intrinsic default, overridable via config.
DEFAULT_ALLOWED_NAMES: frozenset[str] = frozenset(
    {
        "README.md",
        "CHANGELOG.md",
        "CONTRIBUTING.md",
        "SECURITY.md",
        "LICENSE.md",
        "LICENSE",
        "AGENTS.md",
        "VERSION",
        "Makefile",
    }
)

#: Cache/build segments that drop a path from scope. Overridable via config.
DEFAULT_EXEMPT_SEGMENTS: tuple[str, ...] = (
    ".git",
    ".github",
    ".architecture",
    "node_modules",
    "__pycache__",
    ".venv",
    ".pytest_cache",
    ".ruff_cache",
)

REMEDIATION = _remediation(
    fix=(
        "rename the path to lowercase kebab-case for docs/dirs or snake_case "
        "for importable Python; or, if the offender pre-dates the rule, add a "
        "justified entry to the baseline (it may only shrink)."
    ),
    nxt="re-run this check to confirm the rename cleared the violation.",
    run="python -m tc_fitness.core_checks.path_naming",
    passing="docs/my-design-note.md   scripts/checks/my_check.py",
    forbidden="docs/MyDesignNote.md    scripts/checks/My-Check.py",
)


def name_violates_convention(
    rel: str,
    *,
    kebab_roots: tuple[str, ...],
    snake_roots: tuple[str, ...],
    allowed_names: frozenset[str],
) -> bool:
    """True iff repo-relative ``rel`` breaks the convention for its root.

    Pure helper (the detection core) so tests can assert on it directly. A
    ``.md`` path under a kebab root must have a kebab-case stem; a ``.py`` path
    under a snake root must be an importable module name. Always-allowed
    filenames are never flagged. A path under no configured root is clean.
    """
    name = rel.rsplit("/", 1)[-1]
    if name in allowed_names:
        return False
    if rel.endswith(".md") and rel.startswith(kebab_roots):
        stem = name[: -len(".md")]
        return KEBAB_RE.fullmatch(stem) is None
    if rel.endswith(".py") and rel.startswith(snake_roots):
        return SNAKE_RE.fullmatch(name) is None
    return False


class PathNaming(FitnessRule):
    """Flags paths whose name breaks the configured naming convention."""

    name = "path-naming"
    remediation = REMEDIATION
    #: Both doc and Python files are candidates; the root config decides which
    #: convention applies. A consumer narrows via ``extensions`` if desired.
    extensions = (".md", ".py")

    kebab_roots: tuple[str, ...] = ()
    snake_roots: tuple[str, ...] = ()
    allowed_names: frozenset[str] = DEFAULT_ALLOWED_NAMES
    exempt_segments: tuple[str, ...] = DEFAULT_EXEMPT_SEGMENTS

    @classmethod
    def from_config(
        cls,
        config: Mapping[str, Any],
        *,
        repo_root: Path | None = None,
    ) -> PathNaming:
        rule = super().from_config(config, repo_root=repo_root)
        assert isinstance(rule, PathNaming)  # noqa: S101  # narrowing for mypy
        kebab = config.get("kebab_roots")
        if kebab is not None:
            rule.kebab_roots = tuple(kebab)
        snake = config.get("snake_roots")
        if snake is not None:
            rule.snake_roots = tuple(snake)
        allowed = config.get("allowed_names")
        if allowed is not None:
            rule.allowed_names = frozenset(allowed)
        segments = config.get("exempt_segments")
        if segments is not None:
            rule.exempt_segments = tuple(segments)
        return rule

    def is_in_scope(self, rel: str) -> bool:
        if any(seg in self.exempt_segments for seg in rel.split("/")):
            return False
        # Scope is governed by the per-root conventions, not by ``roots``;
        # a path under no convention-root is simply never a violation.
        return rel.endswith(self._extensions)

    def file_has_violation(self, path: Path) -> bool:
        rel = str(self._repo_relative(path)).replace("\\", "/")
        return name_violates_convention(
            rel,
            kebab_roots=self.kebab_roots,
            snake_roots=self.snake_roots,
            allowed_names=self.allowed_names,
        )

    def enumerate_files(self) -> list[Path]:
        """Enumerate under the union of the convention roots.

        ``path_naming`` scopes by ``kebab_roots`` / ``snake_roots`` rather than
        the base ``roots`` knob, so enumeration walks those prefixes. When none
        are configured the rule enumerates nothing (flags nothing).
        """
        out: list[Path] = []
        for root in (*self.kebab_roots, *self.snake_roots):
            root_path = self._repo_root / root
            if not root_path.exists():
                continue
            for path in root_path.rglob("*"):
                if not path.is_file() or "__pycache__" in path.parts:
                    continue
                if path.name.endswith(self._extensions):
                    out.append(path)
        return out


def build(
    config: Mapping[str, Any],
    *,
    repo_root: Path | None = None,
) -> PathNaming:
    """Factory the engine calls to bind this CORE check to a consumer's config."""
    return PathNaming.from_config(config, repo_root=repo_root)


def main(argv: list[str] | None = None) -> int:
    """CLI entry — supports ``--establish-baseline`` and ``--repo-root``."""
    return run_core_check(PathNaming, argv)


if __name__ == "__main__":
    import sys

    sys.exit(main())
