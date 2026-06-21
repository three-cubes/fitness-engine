"""CORE check: ci_silencers_have_rationale — every CI/local silencer is justified.

A CI or local quality silencer (``continue-on-error: true``, ``|| true``,
``--cov-fail-under=0``, ``if: ${{ always() }}``) hides a failing step. Left
undocumented it rots into a permanently-green gate that catches nothing. A
silencer PASSES when the same line OR an adjacent line (within a small window)
carries a rationale token or a trailing ``# <text>`` comment.

Ported from tc-agent-zone ``scripts/checks/ci_silencers.py`` (F10) and
re-expressed as a configurable, repo-agnostic rule. The silencer and rationale
token shapes are CI idioms (the rule's own domain-intrinsic ``DEFAULT_*``),
overridable via config; the workflow directory and the named scan-file list
come from config. No repo paths are baked in.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from tc_fitness.core_checks import run_core_check
from tc_fitness.fitness_rule import FitnessRule
from tc_fitness.lib import remediation as _remediation

#: Rationale tokens that justify a silencer when found nearby — CI idiom, overridable.
DEFAULT_RATIONALE_TOKENS: tuple[str, ...] = (
    "quality-harness",
    "intentional",
    "rationale",
    "non-blocking",
    "best-effort",
    "compatibility",
)
#: Silencer constructs the rule scans for — CI idiom, overridable.
DEFAULT_SILENCER_PATTERNS: tuple[str, ...] = (
    r"continue-on-error\s*:\s*true",
    r"fail_ci_if_error\s*:\s*false",
    r"\|\|\s*true",
    r"--cov-fail-under=0",
    r"if:\s*\$\{\{\s*always\(\)",
)
#: How many lines either side of a silencer count as "nearby". Overridable.
DEFAULT_WINDOW = 2
#: The CI workflow directory globbed for ``*.yml`` / ``*.yaml``. Overridable.
DEFAULT_WORKFLOWS_DIR = ".github/workflows"

REMEDIATION = _remediation(
    fix=(
        "add a same-line trailing # <reason> comment explaining why the silencer "
        "is safe, OR place a comment containing one of the recognised rationale "
        "tokens (quality-harness / intentional / rationale / non-blocking / "
        "best-effort / compatibility) within the configured window."
    ),
    nxt="re-run this check to confirm the gate goes green.",
    run="python -m tc_fitness.core_checks.ci_silencers_have_rationale",
    passing="continue-on-error: true  # non-blocking: advisory lint job",
    forbidden="continue-on-error: true",
)


def _has_nearby_rationale(
    lines: list[str],
    index: int,
    *,
    rationale_re: re.Pattern[str],
    window: int,
) -> bool:
    start = max(0, index - window)
    end = min(len(lines), index + window + 1)
    if rationale_re.search("\n".join(lines[start:end])):
        return True
    line = lines[index]
    if "#" in line and line.split("#", 1)[1].strip():
        return True
    return False


def file_has_unjustified_silencer(
    path: Path,
    *,
    silencer_re: re.Pattern[str],
    rationale_re: re.Pattern[str],
    window: int,
) -> bool:
    """True iff ``path`` has a silencer line lacking a nearby rationale.

    Pure helper (the detection core) so tests assert on it directly. A read
    error is treated as "no violation".
    """
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (UnicodeDecodeError, OSError):
        return False
    for idx, line in enumerate(lines):
        if silencer_re.search(line) and not _has_nearby_rationale(
            lines, idx, rationale_re=rationale_re, window=window
        ):
            return True
    return False


class CiSilencersHaveRationale(FitnessRule):
    """Flags a CI/local file holding a rationale-free quality silencer."""

    name = "ci-silencers-have-rationale"
    remediation = REMEDIATION
    # Enumeration is custom (workflow glob + named scan files), so extension
    # scoping is not used for selection.
    extensions = ()

    #: Rule-specific knobs — overridable per consumer.
    rationale_tokens: tuple[str, ...] = DEFAULT_RATIONALE_TOKENS
    silencer_patterns: tuple[str, ...] = DEFAULT_SILENCER_PATTERNS
    window: int = DEFAULT_WINDOW
    workflows_dir: str = DEFAULT_WORKFLOWS_DIR
    #: Extra named files (shell/python harnesses) to scan; supplied via config.
    scan_files: tuple[str, ...] = ()

    @classmethod
    def from_config(
        cls,
        config: Mapping[str, Any],
        *,
        repo_root: Path | None = None,
    ) -> CiSilencersHaveRationale:
        rule = super().from_config(config, repo_root=repo_root)
        assert isinstance(rule, CiSilencersHaveRationale)  # noqa: S101  # narrowing for mypy
        tokens = config.get("rationale_tokens")
        rule.rationale_tokens = tuple(tokens) if tokens is not None else DEFAULT_RATIONALE_TOKENS
        sil = config.get("silencer_patterns")
        rule.silencer_patterns = tuple(sil) if sil is not None else DEFAULT_SILENCER_PATTERNS
        rule.window = int(config.get("window", DEFAULT_WINDOW))
        rule.workflows_dir = str(config.get("workflows_dir", DEFAULT_WORKFLOWS_DIR))
        scans = config.get("scan_files")
        rule.scan_files = tuple(scans) if scans is not None else ()
        return rule

    def _silencer_re(self) -> re.Pattern[str]:
        return re.compile("|".join(f"(?:{p})" for p in self.silencer_patterns))

    def _rationale_re(self) -> re.Pattern[str]:
        return re.compile("|".join(re.escape(t) for t in self.rationale_tokens), re.IGNORECASE)

    def enumerate_files(self) -> list[Path]:
        """Glob the workflow dir for YAML, plus any explicitly named scan files."""
        out: list[Path] = []
        wf = self._repo_root / self.workflows_dir
        if wf.exists():
            for pattern in ("*.yml", "*.yaml"):
                out.extend(sorted(wf.glob(pattern)))
        for rel in self.scan_files:
            target = self._repo_root / rel
            if target.is_file():
                out.append(target)
        return out

    def is_in_scope(self, rel: str) -> bool:
        """Scope is whatever enumeration returns; exempt-file filtering applies upstream."""
        return True

    def file_has_violation(self, path: Path) -> bool:
        return file_has_unjustified_silencer(
            path,
            silencer_re=self._silencer_re(),
            rationale_re=self._rationale_re(),
            window=self.window,
        )


def build(config: Mapping[str, Any], *, repo_root: Path | None = None) -> CiSilencersHaveRationale:
    """Factory the engine calls to bind this CORE check to a consumer's config."""
    return CiSilencersHaveRationale.from_config(config, repo_root=repo_root)


def main(argv: list[str] | None = None) -> int:
    """CLI entry — supports ``--establish-baseline`` and ``--repo-root``."""
    return run_core_check(CiSilencersHaveRationale, argv)


if __name__ == "__main__":
    import sys

    sys.exit(main())
