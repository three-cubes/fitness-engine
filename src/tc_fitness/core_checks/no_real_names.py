"""CORE check: no_real_names — keep real identities out of fixtures and examples.

Test fixtures, BDD scenarios, and documentation examples must use synthetic
identities, never real client / person / company names. A leaked real name in
a committed example is a confidentiality and reputational hazard that survives
every fork of the fixture. This rule walks the configured fixture / example
surface and FAILS when a banned name token appears outside an allow-list.

Ported from tc-agent-zone ``scripts/checks/no_real_names_in_fixtures.py``
(issue #184) and re-expressed as a configurable, repo-agnostic rule. The
banned-token-to-substitute mapping, the directory surface that is scanned, the
in-scope text extensions, and the allow-list are ALL consumer config — the
engine ships no real names, no synthetic placeholders, and no repo paths. A
consumer supplies its own mapping via ``[tool.tc_fitness]`` (taz uses
Bupa->AcmeHealth, Avanade->NexusDigital, etc.; another repo's mapping differs).
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from tc_fitness.core_checks import run_core_check
from tc_fitness.fitness_rule import FitnessRule
from tc_fitness.lib import remediation as _remediation

#: Domain-intrinsic defaults for the *shape* of the scan (not repo identity).
#: A consumer always supplies its own ``substitutions``; with an empty map the
#: rule is a no-op, so the default never silently passes a real name.
DEFAULT_EXTENSIONS = (
    ".md",
    ".markdown",
    ".txt",
    ".rst",
    ".json",
    ".yaml",
    ".yml",
    ".toml",
    ".py",
    ".ts",
    ".tsx",
    ".js",
    ".jsx",
    ".sh",
    ".html",
    ".xml",
    ".csv",
    ".feature",
    ".jinja",
    ".j2",
    ".tmpl",
)

REMEDIATION = _remediation(
    fix=(
        "substitute the real name with the synthetic placeholder your standard "
        "maps it to (the consumer's substitution map names the replacement); OR, "
        "if the file is legitimately authoritative, add its repo-relative path to "
        "the configured allow-list with a justifying comment."
    ),
    nxt="re-run this check to confirm it goes green.",
    run="python -m tc_fitness.core_checks.no_real_names",
    passing='client = "AcmeHealth"  # synthetic substitute, never the real client',
    forbidden='client = "<real-company-name>"  # leaks a real identity into a fixture',
)


def _compile_token(token: str) -> re.Pattern[str]:
    """Word-boundary matcher tolerating digit-bearing tokens (e.g. ``3CV``).

    A plain ``\\b`` boundary fails next to a digit at a word edge, so this uses
    explicit alphanumeric look-arounds: the token matches only when not flanked
    by another alphanumeric char.
    """
    return re.compile(rf"(?<![A-Za-z0-9]){re.escape(token)}(?![A-Za-z0-9])")


def file_has_real_name(
    path: Path,
    *,
    tokens: Sequence[str],
    scope_segments: Sequence[str],
    repo_root: Path,
) -> bool:
    """True iff ``path`` is in the scan surface AND contains a banned token.

    ``scope_segments`` are path segments any one of which must appear in the
    file's repo-relative path for it to be in scope (e.g. an examples directory
    anywhere in the ancestry). An empty ``scope_segments`` means every in-scope
    extension is scanned. A decode error returns False (binary / unreadable
    files carry no readable identity to leak).
    """
    try:
        rel = path.resolve().relative_to(repo_root).as_posix()
    except ValueError:
        rel = path.as_posix()
    if scope_segments and not any(seg in rel.split("/") for seg in scope_segments):
        return False
    try:
        text = path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        return False
    return any(_compile_token(tok).search(text) for tok in tokens)


class NoRealNames(FitnessRule):
    """Flags fixtures / examples that carry a banned real-name token."""

    name = "no-real-names"
    remediation = REMEDIATION
    extensions = DEFAULT_EXTENSIONS

    #: Rule-specific config (instance attrs so from_config can override them).
    #: ``tokens`` is the set of banned name literals the consumer supplies;
    #: ``scope_segments`` narrows the scan to directories whose name appears as
    #: a path segment (e.g. an ``examples`` dir anywhere in the tree).
    tokens: tuple[str, ...] = ()
    scope_segments: tuple[str, ...] = ()

    @classmethod
    def from_config(
        cls,
        config: Mapping[str, Any],
        *,
        repo_root: Path | None = None,
    ) -> NoRealNames:
        rule = super().from_config(config, repo_root=repo_root)
        assert isinstance(rule, NoRealNames)  # noqa: S101  # narrowing for mypy
        # Accept either a flat list of banned tokens, or a {token: substitute}
        # map (the substitute is surfaced only in remediation prose, so the
        # detector keys on the token set either way).
        raw_tokens = config.get("tokens")
        if raw_tokens is None:
            mapping = config.get("substitutions")
            raw_tokens = list(mapping) if isinstance(mapping, Mapping) else []
        rule.tokens = tuple(raw_tokens)
        rule.scope_segments = tuple(config.get("scope_segments", ()))
        return rule

    def file_has_violation(self, path: Path) -> bool:
        if not self.tokens:
            return False
        return file_has_real_name(
            path,
            tokens=self.tokens,
            scope_segments=self.scope_segments,
            repo_root=self._repo_root,
        )


def build(
    config: Mapping[str, Any],
    *,
    repo_root: Path | None = None,
) -> NoRealNames:
    """Factory the engine calls to bind this CORE check to a consumer's config."""
    return NoRealNames.from_config(config, repo_root=repo_root)


def main(argv: list[str] | None = None) -> int:
    """CLI entry — supports ``--establish-baseline`` and ``--repo-root``."""
    return run_core_check(NoRealNames, argv)


if __name__ == "__main__":
    import sys

    sys.exit(main())
