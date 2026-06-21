"""CORE check: no_internal_patches_ts — TS tests must not mock internal modules.

The TypeScript companion to :mod:`tc_fitness.core_checks.no_internal_patches`.
``vi.mock('../src/foo.js')`` / ``vi.spyOn(internalNs, 'fn')`` (and the ``jest``
equivalents) are the same inappropriate-intimacy anti-pattern wearing a
different language hat: simulating composition instead of exercising it. The
right unit of work is to construct the unit under test with explicit fakes
passed through its constructor / call signature.

Ported from tc-agent-zone ``scripts/checks/no_internal_patches_ts.py`` and
re-expressed as a configurable, repo-agnostic rule. The regex shapes (the mock
/ spy / import grammar) are domain-intrinsic; what was repo-specific is now
consumer config: ``internal_packages`` (workspace package names whose mocking
is the smell), ``exempt_specifiers`` (exact external module names), and
``exempt_prefixes`` (external scopes like an SDK namespace). Relative-path
specifiers (``./`` / ``../``) are always internal. The engine ships NO repo
package names.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from tc_fitness.core_checks import run_core_check
from tc_fitness.fitness_rule import FitnessRule
from tc_fitness.lib import remediation as _remediation

REMEDIATION = _remediation(
    fix=(
        "rewrite the test to inject a fake (HTTP client, filesystem adapter, "
        "etc.) through the function/class signature; if the production code "
        "lacks a DI seam, add one. Mocking internal modules hides composition "
        "failures."
    ),
    nxt="re-run this check to confirm the file falls off the violator list.",
    run="python -m tc_fitness.core_checks.no_internal_patches_ts",
    passing="const server = buildServer({ graphClient: fakeGraph, fs: new MemFs() })",
    forbidden="vi.mock('../../src/client.js', () => ({ graphGet: vi.fn() }))",
)

# Domain-intrinsic grammar (the mock / spy / import shapes). Repo-neutral.
_RX_MOCK_STRING = re.compile(
    r"""\b(?:vi|jest)\.(?:do)?[Mm]ock\s*\(\s*(['"`])([^'"`]+)\1""",
    re.MULTILINE,
)
_RX_SPY_ON = re.compile(
    r"""\b(?:vi|jest)\.spyOn\s*\(\s*([A-Za-z_$][\w$]*)\s*,""",
    re.MULTILINE,
)
_RX_IMPORT_NS = re.compile(
    r"""^\s*import\s+\*\s+as\s+([A-Za-z_$][\w$]*)\s+from\s+(['"`])([^'"`]+)\2""",
    re.MULTILINE,
)
_RX_IMPORT_DEFAULT = re.compile(
    r"""^\s*import\s+([A-Za-z_$][\w$]*)(?:\s*,\s*\{[^}]*\})?\s+from\s+(['"`])([^'"`]+)\2""",
    re.MULTILINE,
)
_RX_IMPORT_NAMED = re.compile(
    r"""^\s*import\s+\{([^}]+)\}\s+from\s+(['"`])([^'"`]+)\2""",
    re.MULTILINE,
)
_RX_LINE_COMMENT = re.compile(r"//[^\n]*")
_RX_BLOCK_COMMENT = re.compile(r"/\*[\s\S]*?\*/", re.MULTILINE)
_RX_IDENT = re.compile(r"[A-Za-z_$][\w$]*")


def _strip_comments(text: str) -> str:
    """Strip JS/TS comments so example snippets in comments don't fire the gate."""
    text = _RX_BLOCK_COMMENT.sub("", text)
    return _RX_LINE_COMMENT.sub("", text)


def _is_internal_specifier(spec: str, internal_packages: frozenset[str]) -> bool:
    """True iff ``spec`` resolves to an internal module.

    A relative path (``./`` / ``../``) is always internal; otherwise the first
    path segment must be a configured internal workspace package.
    """
    if spec.startswith("./") or spec.startswith("../"):
        return True
    return spec.split("/", 1)[0] in internal_packages


def _is_exempt_specifier(spec: str, exempt_exact: frozenset[str], exempt_prefixes: tuple[str, ...]) -> bool:
    if spec in exempt_exact:
        return True
    return any(spec.startswith(prefix) for prefix in exempt_prefixes)


def _resolve_imports(text: str) -> dict[str, str]:
    """Map each imported name -> its source specifier (for spyOn resolution)."""
    out: dict[str, str] = {}
    for m in _RX_IMPORT_NS.finditer(text):
        out[m.group(1)] = m.group(3)
    for m in _RX_IMPORT_DEFAULT.finditer(text):
        out.setdefault(m.group(1), m.group(3))
    for m in _RX_IMPORT_NAMED.finditer(text):
        spec = m.group(3)
        for raw in m.group(1).split(","):
            name = raw.strip().split(" as ")[-1].strip()
            if name and _RX_IDENT.fullmatch(name):
                out.setdefault(name, spec)
    return out


def file_mocks_internal_ts(
    path: Path,
    *,
    internal_packages: frozenset[str],
    exempt_exact: frozenset[str],
    exempt_prefixes: tuple[str, ...],
) -> bool:
    """Pure detection helper: True iff ``path`` mocks/spies an internal module.

    A read error is treated as "no violation" (another check owns unreadable
    files).
    """
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return False
    stripped = _strip_comments(text)
    for m in _RX_MOCK_STRING.finditer(stripped):
        spec = m.group(2)
        if _is_exempt_specifier(spec, exempt_exact, exempt_prefixes):
            continue
        if _is_internal_specifier(spec, internal_packages):
            return True
    imports = _resolve_imports(stripped)
    for m in _RX_SPY_ON.finditer(stripped):
        spec = imports.get(m.group(1))
        if spec is None:
            continue
        if _is_exempt_specifier(spec, exempt_exact, exempt_prefixes):
            continue
        if _is_internal_specifier(spec, internal_packages):
            return True
    return False


class NoInternalPatchesTs(FitnessRule):
    """Flags TS/TSX test files that mock/spy an INTERNAL module (F1-TS)."""

    name = "no-internal-patches-ts"
    remediation = REMEDIATION
    extensions = (".test.ts", ".test.tsx")

    #: Rule-specific config (instance attrs; from_config overrides per consumer).
    internal_packages: frozenset[str] = frozenset()
    exempt_specifiers: frozenset[str] = frozenset()
    exempt_prefixes: tuple[str, ...] = ()

    @classmethod
    def from_config(
        cls,
        config: Mapping[str, Any],
        *,
        repo_root: Path | None = None,
    ) -> NoInternalPatchesTs:
        rule = super().from_config(config, repo_root=repo_root)
        assert isinstance(rule, NoInternalPatchesTs)  # noqa: S101  # narrowing for mypy
        rule.internal_packages = frozenset(config.get("internal_packages", ()))
        rule.exempt_specifiers = frozenset(config.get("exempt_specifiers", ()))
        rule.exempt_prefixes = tuple(config.get("exempt_prefixes", ()))
        return rule

    def file_has_violation(self, path: Path) -> bool:
        return file_mocks_internal_ts(
            path,
            internal_packages=self.internal_packages,
            exempt_exact=self.exempt_specifiers,
            exempt_prefixes=self.exempt_prefixes,
        )


def build(
    config: Mapping[str, Any],
    *,
    repo_root: Path | None = None,
) -> NoInternalPatchesTs:
    """Factory the engine calls to bind this CORE check to a consumer's config."""
    return NoInternalPatchesTs.from_config(config, repo_root=repo_root)


def main(argv: list[str] | None = None) -> int:
    """CLI entry — supports ``--establish-baseline`` and ``--repo-root``."""
    return run_core_check(NoInternalPatchesTs, argv)


if __name__ == "__main__":
    import sys

    sys.exit(main())
