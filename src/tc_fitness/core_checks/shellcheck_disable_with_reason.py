"""CORE check: shellcheck_disable_with_reason — every shell silencer is justified.

A bare ``# shellcheck disable=SC2034`` is a silent override — six months later
nobody knows whether it is still load-bearing or whether the underlying warning
has become a real bug. An inline rationale (or one on the immediately preceding
``#`` comment line) documents WHY the rule doesn't apply. The shell counterpart
to the Python suppression-rationale rule.

Ported from kairix ``scripts/checks/check_shellcheck_disable_with_reason.py``
(F33) and re-expressed as a configurable, repo-agnostic, baseline-gated rule.
The disable-directive shape, the rationale markers, and the minimum rationale
length are the rule's own shape (``DEFAULT_*``), overridable via config; the
consumer supplies ``roots`` / ``exempt_files``. No repo paths are baked in.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from tc_fitness.core_checks import run_core_check
from tc_fitness.fitness_rule import FitnessRule
from tc_fitness.lib import remediation as _remediation

# Matches ``# shellcheck disable=SC2034`` (and comma-lists), capturing the tail.
_DISABLE_RE = re.compile(r"#\s*shellcheck\s+disable=(?P<rules>[A-Za-z0-9,]+)(?P<trailing>.*)$")
# A file is a shell script if its name ends in ``.sh`` OR its first line is a
# recognised shell shebang.
_SHEBANG_RE = re.compile(r"^#!\s*(?:/usr/bin/env\s+)?(?:ba)?sh\b")

#: Canonical rationale-marker prefixes — the rule's own shape, overridable.
DEFAULT_RATIONALE_MARKERS: tuple[str, ...] = (
    "fix:",
    "next:",
    "run:",
    "why:",
    "rationale:",
    "reason:",
    "because:",
)
#: Minimum free-text rationale length below which a comment is a stub. Overridable.
DEFAULT_MIN_RATIONALE_LEN = 10

REMEDIATION = _remediation(
    fix=(
        "add an inline comment after the directive that explains WHY the rule "
        "doesn't apply (an em-dash + one-line justification is the canonical "
        "shape), OR put the rationale on the immediately preceding # comment "
        "line; the markers fix:/next:/run:/why:/rationale:/reason:/because: are "
        "recognised."
    ),
    nxt="re-run this check to confirm the gate goes green.",
    run="python -m tc_fitness.core_checks.shellcheck_disable_with_reason",
    passing="# shellcheck disable=SC2034  # exported via process substitution below",
    forbidden="# shellcheck disable=SC1090",
)


def _is_rationale_comment(line: str, markers: tuple[str, ...], min_len: int) -> bool:
    if not line.startswith("#") or line.startswith("#!"):
        return False
    body = line.lstrip("#").strip()
    if not body:
        return False
    lowered = body.lower()
    if any(marker in lowered for marker in markers):
        return True
    if "shellcheck" in lowered and "disable" in lowered:
        return False
    return len(body) >= min_len


def _trailing_has_rationale(trailing: str, markers: tuple[str, ...], min_len: int) -> bool:
    tail = trailing.strip()
    if not tail:
        return False
    if tail.startswith("#"):
        return _is_rationale_comment(tail, markers, min_len)
    lowered = tail.lower()
    if any(marker in lowered for marker in markers):
        return True
    return len(tail) >= min_len


def _preceding_line_has_rationale(lines: list[str], idx: int, markers: tuple[str, ...], min_len: int) -> bool:
    j = idx - 1
    while j >= 0 and not lines[j].strip():
        j -= 1
    if j < 0:
        return False
    return _is_rationale_comment(lines[j].strip(), markers, min_len)


def is_shell_file(path: Path) -> bool:
    """True if ``path`` is a ``.sh`` file or its first line is a shell shebang."""
    if path.name.endswith(".sh"):
        return True
    try:
        with path.open("r", encoding="utf-8", errors="ignore") as fh:
            first = fh.readline()
    except OSError:
        return False
    return bool(_SHEBANG_RE.match(first))


def file_has_unjustified_disable(
    path: Path,
    *,
    markers: tuple[str, ...],
    min_len: int,
) -> bool:
    """True iff ``path`` has a shellcheck-disable lacking a same/preceding-line reason.

    Pure helper (the detection core) so tests assert on it directly. A read
    error is treated as "no violation".
    """
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (UnicodeDecodeError, OSError):
        return False
    for idx, line in enumerate(lines):
        m = _DISABLE_RE.search(line)
        if (
            m is not None
            and not _trailing_has_rationale(m.group("trailing"), markers, min_len)
            and not _preceding_line_has_rationale(lines, idx, markers, min_len)
        ):
            return True
    return False


class ShellcheckDisableWithReason(FitnessRule):
    """Flags a shell file holding a rationale-free ``# shellcheck disable=`` directive."""

    name = "shellcheck-disable-with-reason"
    remediation = REMEDIATION
    # Shell scope is shebang-aware, so enumeration/scope are overridden below;
    # ``.sh`` is the cheap extension prefilter.
    extensions = (".sh",)

    #: Rule-specific knobs — overridable per consumer.
    rationale_markers: tuple[str, ...] = DEFAULT_RATIONALE_MARKERS
    min_rationale_len: int = DEFAULT_MIN_RATIONALE_LEN

    @classmethod
    def from_config(
        cls,
        config: Mapping[str, Any],
        *,
        repo_root: Path | None = None,
    ) -> ShellcheckDisableWithReason:
        rule = super().from_config(config, repo_root=repo_root)
        assert isinstance(rule, ShellcheckDisableWithReason)  # noqa: S101  # narrowing for mypy
        markers = config.get("rationale_markers")
        rule.rationale_markers = tuple(markers) if markers is not None else DEFAULT_RATIONALE_MARKERS
        rule.min_rationale_len = int(config.get("min_rationale_len", DEFAULT_MIN_RATIONALE_LEN))
        return rule

    def enumerate_files(self) -> list[Path]:
        """Walk configured roots, returning shell files (``.sh`` or shebang)."""
        out: list[Path] = []
        for root in self._roots:
            root_path = self._repo_root / root
            if not root_path.exists():
                continue
            for path in root_path.rglob("*"):
                if not path.is_file() or "__pycache__" in path.parts:
                    continue
                if is_shell_file(path):
                    out.append(path)
        return out

    def is_in_scope(self, rel: str) -> bool:
        """Scope is whatever enumeration returns; exempt-file filtering applies upstream."""
        return True

    def file_has_violation(self, path: Path) -> bool:
        return file_has_unjustified_disable(
            path,
            markers=self.rationale_markers,
            min_len=self.min_rationale_len,
        )


def build(config: Mapping[str, Any], *, repo_root: Path | None = None) -> ShellcheckDisableWithReason:
    """Factory the engine calls to bind this CORE check to a consumer's config."""
    return ShellcheckDisableWithReason.from_config(config, repo_root=repo_root)


def main(argv: list[str] | None = None) -> int:
    """CLI entry — supports ``--establish-baseline`` and ``--repo-root``."""
    return run_core_check(ShellcheckDisableWithReason, argv)


if __name__ == "__main__":
    import sys

    sys.exit(main())
