"""CORE check: no_llm_attribution — no AI/LLM self-attribution residue.

Machine-enforced clean authorship (Autonomous Delivery Platform decision D1,
SP-A / SGO-156): agent work is authored by the accountable bot/human identity,
never advertised as model-generated. This rule FAILS when AI-attribution
residue appears in the repo — a ``Co-Authored-By: Claude`` trailer, a
``Generated with <AI tool>`` credit, an AI-vendor ``noreply`` author email, or
the robot emoji (U+1F916) that tools append to commit/PR metadata.

Unlike most CORE checks, the banned set here is **intrinsic, not repo config**:
the attribution *signatures* are provider-neutral and universal, so the engine
ships them as sensible defaults (a consumer only supplies the scan ``roots`` /
``extensions`` and, where a legitimate in-source use exists, an ``exempt_files``
entry or a grandfathering baseline).

Two surfaces share ONE detector, :func:`scan_text`:

* the :class:`FitnessRule` below scans in-repo files for residue, and
* the standalone :func:`scan_text` is reused by the shipped commit-msg strip
  hook (SGO-159) and the CI trailer-reject leg (SGO-160) to scan commit
  messages and PR title/body — so the pattern set is single-sourced and can
  never drift between the local hook, CI, and the fitness gate.

Guard-forward (decision D2): pre-cutover residue is grandfathered via the
per-file baseline (``--establish-baseline``); only NET-NEW residue fails.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any, NamedTuple

from tc_fitness.core_checks import run_core_check
from tc_fitness.fitness_rule import FitnessRule
from tc_fitness.lib import remediation as _remediation

#: The robot emoji (U+1F916) tools append to Claude Code commit/PR metadata.
_ROBOT = "\U0001f916"

#: Provider-neutral AI/agent tool tokens that make a trailer/credit an
#: *attribution* rather than an ordinary mention.
_AI_TOOL = r"(?:claude|anthropic|cursor|copilot|codex|gemini|chatgpt|gpt-?[0-9]|an ai|ai assist(?:ant|ance))"

#: (signature-name, compiled pattern). Intrinsic + provider-neutral — a bare
#: mention of a vendor's *name* is NOT matched; only attribution SIGNATURES are.
SIGNATURES: tuple[tuple[str, re.Pattern[str]], ...] = (
    # A Co-Authored-By / Signed-off-by / Assisted-by trailer that names an AI tool.
    (
        "attribution_trailer",
        re.compile(
            rf"(?im)^[ \t]*(?:co-authored-by|signed-off-by|assisted-by|generated-by)\s*:[^\n]*\b{_AI_TOOL}\b"
        ),
    ),
    # "Generated with [Claude Code]" / "generated with an AI" style credit lines.
    ("generated_with", re.compile(rf"(?i)generated (?:with|by)\s+\[?{_AI_TOOL}\b")),
    # An AI-vendor no-reply author/committer email.
    ("anthropic_noreply", re.compile(r"(?i)\bnoreply@anthropic\.com\b")),
    # The robot emoji tools stamp onto commit/PR metadata.
    ("robot_emoji", re.compile(_ROBOT)),
)

DEFAULT_EXTENSIONS = (
    ".md",
    ".markdown",
    ".txt",
    ".rst",
    ".py",
    ".ts",
    ".tsx",
    ".js",
    ".jsx",
    ".sh",
    ".yaml",
    ".yml",
    ".toml",
    ".json",
    ".html",
    ".feature",
)

REMEDIATION = _remediation(
    fix=(
        "remove the AI/LLM self-attribution — strip the `Co-Authored-By: <model>` / "
        "`Generated with <tool>` trailer, the robot emoji, or the `noreply@anthropic.com` "
        "identity. Agent work is authored by the canonical bot/human, never advertised as "
        "model-generated. If an in-source string is genuinely functional (names the tool "
        "without claiming authorship), add its path to this check's `exempt_files`."
    ),
    nxt="re-run this check to confirm it goes green.",
    run="python -m tc_fitness.core_checks.no_llm_attribution",
    passing="Co-Authored-By: Jane Doe <jane@example.com>   # a real accountable human",
    forbidden="Co-Authored-By: Claude <noreply@anthropic.com>   # AI self-attribution",
)


class Hit(NamedTuple):
    """One attribution-signature match: which signature, and the matched text."""

    signature: str
    match: str


def scan_text(text: str) -> list[Hit]:
    """Return every AI-attribution signature match in ``text`` (empty == clean).

    The single detector shared by the file scan, the commit-msg strip hook, and
    the CI trailer-reject leg. Keys on attribution *signatures* (trailers,
    credits, vendor no-reply emails, the robot emoji) — a bare mention of a
    vendor's name in ordinary prose or a genuine human co-author trailer does
    NOT match.
    """
    hits: list[Hit] = []
    for name, pattern in SIGNATURES:
        for m in pattern.finditer(text):
            hits.append(Hit(name, m.group(0)))
    return hits


class NoLlmAttribution(FitnessRule):
    """Flags files carrying AI/LLM self-attribution residue."""

    name = "no-llm-attribution"
    remediation = REMEDIATION
    extensions = DEFAULT_EXTENSIONS

    def file_has_violation(self, path: Path) -> bool:
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            return False
        return bool(scan_text(text))


def build(
    config: Mapping[str, Any],
    *,
    repo_root: Path | None = None,
) -> NoLlmAttribution:
    """Factory the engine calls to bind this CORE check to a consumer's config."""
    rule = NoLlmAttribution.from_config(config, repo_root=repo_root)
    assert isinstance(rule, NoLlmAttribution)  # noqa: S101  # narrowing for mypy
    return rule


def main(argv: list[str] | None = None) -> int:
    """CLI entry — supports ``--establish-baseline`` and ``--repo-root``."""
    return run_core_check(NoLlmAttribution, argv)


if __name__ == "__main__":
    import sys

    sys.exit(main())
