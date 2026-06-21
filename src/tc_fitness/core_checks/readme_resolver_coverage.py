"""CORE check: readme_resolver_coverage — every top-level directory has a resolver.

A repo is navigable when each top-level directory carries a resolver README that
tells a reader what belongs there and what does not. This rule flags any
top-level directory (under the configured scan roots) that is MISSING the
resolver file, so the "where does X live?" affordance never silently rots as new
top-level domains land.

The violation is an ABSENCE: a scanned directory lacking the resolver file. The
rule enumerates the immediate child directories of each configured root (default
the repo root itself) and flags those without the marker, unless the directory
name is on the exempt list (cache/tooling dirs that carry no information
architecture).

Ported from tc-agent-zone ``scripts/checks/repo_ia.py`` (the IA1
``check_top_level_readmes`` gate, FEAT-145) and re-expressed as a configurable,
repo-agnostic rule. The donor hardcoded a fixed exempt set and the ``README.md``
name; here both are config:

* ``roots`` — prefixes whose immediate child dirs must each carry the resolver
  (default ``("",)`` — the repo root, i.e. top-level directories).
* ``resolver_file`` — the marker filename a directory must contain.
* ``exempt_dirs`` — directory names that carry no IA and are skipped.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from tc_fitness.core_checks import run_core_check
from tc_fitness.fitness_rule import FitnessRule
from tc_fitness.lib import remediation as _remediation

#: The resolver marker every scanned directory must carry. Overridable.
DEFAULT_RESOLVER_FILE = "README.md"

#: Cache/tooling directory names that carry no information architecture and are
#: skipped. Domain-intrinsic default, overridable via config.
DEFAULT_EXEMPT_DIRS: frozenset[str] = frozenset(
    {
        ".git",
        ".github",
        ".pytest_cache",
        ".ruff_cache",
        "__pycache__",
        "node_modules",
        ".venv",
        "logs",
        "htmlcov",
    }
)

REMEDIATION = _remediation(
    fix=(
        "add a resolver README to the directory explaining what belongs there "
        "and what does not, so the 'where does X live?' affordance stays "
        "current; or add the directory's name to the rule's exempt_dirs config "
        "if it carries no information architecture."
    ),
    nxt="re-run this check to confirm the directory now resolves.",
    run="python -m tc_fitness.core_checks.readme_resolver_coverage",
    passing="platform/README.md",
    forbidden="platform/   (no README.md)",
)


def directory_missing_resolver(directory: Path, *, resolver_file: str) -> bool:
    """True iff ``directory`` lacks the ``resolver_file`` marker.

    Pure helper (the detection core) so tests can assert on it directly.
    """
    return not (directory / resolver_file).is_file()


class ReadmeResolverCoverage(FitnessRule):
    """Flags top-level directories missing a resolver README."""

    name = "readme-resolver-coverage"
    remediation = REMEDIATION
    extensions = ()

    #: Default scan root is the repo root itself ("" prefix), i.e. its
    #: top-level directories. A consumer may point at sub-trees instead.
    #: (No re-annotation — ``roots`` is the base ClassVar; we only set the value.)
    roots = ("",)
    resolver_file: str = DEFAULT_RESOLVER_FILE
    exempt_dirs: frozenset[str] = DEFAULT_EXEMPT_DIRS

    @classmethod
    def from_config(
        cls,
        config: Mapping[str, Any],
        *,
        repo_root: Path | None = None,
    ) -> ReadmeResolverCoverage:
        rule = super().from_config(config, repo_root=repo_root)
        assert isinstance(rule, ReadmeResolverCoverage)  # noqa: S101  # narrowing for mypy
        resolver_file = config.get("resolver_file")
        if resolver_file is not None:
            rule.resolver_file = str(resolver_file)
        exempt = config.get("exempt_dirs")
        if exempt is not None:
            rule.exempt_dirs = frozenset(exempt)
        return rule

    def is_in_scope(self, rel: str) -> bool:
        # Scope is the enumerated directories; every enumerated entry is scoped.
        return True

    def enumerate_files(self) -> list[Path]:
        """Enumerate the immediate child directories of each configured root.

        These are the directories that must each carry a resolver — the rule's
        "files" are directories, and ``file_has_violation`` checks for an
        ABSENT marker inside each.
        """
        out: list[Path] = []
        for root in self._roots:
            base = self._repo_root / root if root else self._repo_root
            if not base.is_dir():
                continue
            for child in sorted(base.iterdir()):
                if not child.is_dir():
                    continue
                if child.name in self.exempt_dirs or child.name.startswith("."):
                    continue
                out.append(child)
        return out

    def file_has_violation(self, path: Path) -> bool:
        return directory_missing_resolver(path, resolver_file=self.resolver_file)


def build(
    config: Mapping[str, Any],
    *,
    repo_root: Path | None = None,
) -> ReadmeResolverCoverage:
    """Factory the engine calls to bind this CORE check to a consumer's config."""
    return ReadmeResolverCoverage.from_config(config, repo_root=repo_root)


def main(argv: list[str] | None = None) -> int:
    """CLI entry — supports ``--establish-baseline`` and ``--repo-root``."""
    return run_core_check(ReadmeResolverCoverage, argv)


if __name__ == "__main__":
    import sys

    sys.exit(main())
