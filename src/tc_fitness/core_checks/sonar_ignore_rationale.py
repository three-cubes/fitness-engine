"""CORE check: sonar_ignore_rationale — every Sonar rule-ignore is justified.

A SonarCloud ``sonar.issue.ignore.multicriteria.<id>.ruleKey`` entry is a
load-bearing decision that needs visible justification. This check verifies
every such line in the project's Sonar properties file is preceded by a comment
block explaining WHY the rule is ignored — not just THAT it is. A bare or
TODO-only comment block does not count.

Ported from kairix ``scripts/checks/check_sonar_ignore_rationale.py`` (F14) and
re-expressed as a configurable, repo-agnostic, baseline-gated rule. The Sonar
properties filename and the rule-key pattern are the rule's own shape
(``DEFAULT_SONAR_FILE`` / ``DEFAULT_RULE_KEY_PATTERN``), overridable via config.
The file is the unit baselined, so this rule slots into the standard
``--establish-baseline`` adoption flow like every other CORE check.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from tc_fitness.core_checks import run_core_check
from tc_fitness.fitness_rule import FitnessRule
from tc_fitness.lib import remediation as _remediation

#: The conventional Sonar properties filename — overridable per consumer.
DEFAULT_SONAR_FILE = "sonar-project.properties"
#: The multicriteria rule-key line shape — Sonar's own format, overridable.
DEFAULT_RULE_KEY_PATTERN = r"^sonar\.issue\.ignore\.multicriteria\.([A-Za-z0-9_-]+)\.ruleKey="

_BAD_TOKENS = ("TODO", "FIXME", "XXX", "fixme", "todo")
_MIN_SUBSTANTIVE_LEN = 25

REMEDIATION = _remediation(
    fix=(
        "add a comment block immediately above each multicriteria .ruleKey line "
        "explaining WHY the rule is ignored — at least one comment line with an "
        "em-dash and a substantive sentence (TODO/FIXME placeholders do not "
        "count)."
    ),
    nxt="re-run this check to confirm the gate goes green.",
    run="python -m tc_fitness.core_checks.sonar_ignore_rationale",
    passing="# python:S5547 - HMAC-SHA1 used only for legacy fingerprinting, never security.",
    forbidden="# TODO",
)


def _rationale_lines_above(lines: list[str], index: int) -> list[str]:
    """Collect the contiguous comment block ending immediately above ``index``.

    Walks upward through ``#``-prefixed lines, skipping at most one blank line
    between paragraphs; stops at the first non-comment, non-blank line.
    """
    out: list[str] = []
    i = index - 1
    blank_skipped = False
    while i >= 0:
        line = lines[i].rstrip()
        if line.lstrip().startswith("#"):
            out.append(line.lstrip()[1:].strip())
            blank_skipped = False
        elif line == "":
            if blank_skipped:
                break
            blank_skipped = True
        else:
            break
        i -= 1
    return out


def _has_real_rationale(comment_block: list[str]) -> bool:
    """True if the block has an em-dash or a long substantive (non-TODO) line."""
    for line in comment_block:
        if not line or any(tok in line for tok in _BAD_TOKENS):
            continue
        if "—" in line or "--" in line:
            return True
        if len(line) >= _MIN_SUBSTANTIVE_LEN and not line.startswith("="):
            return True
    return False


def file_has_unjustified_ignore(path: Path, *, rule_key_pattern: str) -> bool:
    """True iff ``path`` has a Sonar rule-ignore lacking a preceding rationale.

    Pure helper (the detection core) so tests assert on it directly. A read
    error is treated as "no violation".
    """
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (UnicodeDecodeError, OSError):
        return False
    rule_re = re.compile(rule_key_pattern)
    for idx, raw in enumerate(lines):
        if rule_re.match(raw.strip()) and not _has_real_rationale(_rationale_lines_above(lines, idx)):
            return True
    return False


class SonarIgnoreRationale(FitnessRule):
    """Flags a Sonar rule-ignore entry that lacks a preceding rationale comment."""

    name = "sonar-ignore-rationale"
    remediation = REMEDIATION
    # Scope is a single named properties file, not an extension family.
    extensions = ()

    #: Rule-specific knobs — overridable per consumer.
    sonar_file: str = DEFAULT_SONAR_FILE
    rule_key_pattern: str = DEFAULT_RULE_KEY_PATTERN

    @classmethod
    def from_config(
        cls,
        config: Mapping[str, Any],
        *,
        repo_root: Path | None = None,
    ) -> SonarIgnoreRationale:
        rule = super().from_config(config, repo_root=repo_root)
        assert isinstance(rule, SonarIgnoreRationale)  # noqa: S101  # narrowing for mypy
        rule.sonar_file = str(config.get("sonar_file", DEFAULT_SONAR_FILE))
        rule.rule_key_pattern = str(config.get("rule_key_pattern", DEFAULT_RULE_KEY_PATTERN))
        return rule

    def enumerate_files(self) -> list[Path]:
        """Scan exactly the configured Sonar properties file (if present)."""
        target = self._repo_root / self.sonar_file
        return [target] if target.is_file() else []

    def is_in_scope(self, rel: str) -> bool:
        """The single named target file is always in scope."""
        return rel == self.sonar_file

    def file_has_violation(self, path: Path) -> bool:
        return file_has_unjustified_ignore(path, rule_key_pattern=self.rule_key_pattern)


def build(config: Mapping[str, Any], *, repo_root: Path | None = None) -> SonarIgnoreRationale:
    """Factory the engine calls to bind this CORE check to a consumer's config."""
    return SonarIgnoreRationale.from_config(config, repo_root=repo_root)


def main(argv: list[str] | None = None) -> int:
    """CLI entry — supports ``--establish-baseline`` and ``--repo-root``."""
    return run_core_check(SonarIgnoreRationale, argv)


if __name__ == "__main__":
    import sys

    sys.exit(main())
