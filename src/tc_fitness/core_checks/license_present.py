"""CORE check: license_present — every source file carries a license header.

Source files distributed without a license / SPDX header strip provenance: a
copied file loses the terms it was released under, and an audit can't tell
which license governs a given module. This rule walks the configured source
surface and FAILS on any file missing a recognised license marker near its
top.

Built fresh for the v0.6.0 CORE set (no single donor — taz/kairix enforce
license/NOTICE provenance via ad-hoc checks). Re-expressed as a configurable,
repo-agnostic rule: the marker strings that count as "a header is present"
(``SPDX-License-Identifier``, ``Copyright``, a license name) and the header
scan window are consumer config. The engine ships generic defaults (the
SPDX/Copyright shape every license header shares), not any repo's chosen
license.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from tc_fitness.core_checks import run_core_check
from tc_fitness.fitness_rule import FitnessRule
from tc_fitness.lib import remediation as _remediation

#: Domain-intrinsic markers every license header shares — not a chosen license.
DEFAULT_MARKERS: tuple[str, ...] = (
    "SPDX-License-Identifier",
    "Copyright",
    "Licensed under",
)
#: How many leading lines of a file are scanned for a marker. A header lives at
#: the very top; scanning the whole file would false-pass on an in-body mention.
DEFAULT_HEADER_LINES = 20

REMEDIATION = _remediation(
    fix=(
        "add a license header to the top of the file — an "
        "`SPDX-License-Identifier: <license>` line (or your repo's standard "
        "copyright / `Licensed under` block) within the first lines of the file."
    ),
    nxt="re-run this check to confirm the gate goes green.",
    run="python -m tc_fitness.core_checks.license_present",
    passing="# SPDX-License-Identifier: MIT",
    forbidden="# (no license header anywhere near the top of the file)",
)


def file_missing_license(path: Path, *, markers: Sequence[str], header_lines: int) -> bool:
    """Pure detection helper: True iff none of ``markers`` appears in the header.

    Only the first ``header_lines`` lines are inspected so an in-body mention of
    a marker word does not false-pass. A read / decode error is treated as "not
    a violation" (a binary file under a misconfigured root is another check's
    concern, not a missing-header one).
    """
    try:
        with path.open(encoding="utf-8") as fh:
            head = "".join(line for _, line in zip(range(header_lines), fh, strict=False))
    except (UnicodeDecodeError, OSError):
        return False
    return not any(marker in head for marker in markers)


class LicensePresent(FitnessRule):
    """Flags source files missing a license / SPDX header."""

    name = "license-present"
    remediation = REMEDIATION
    extensions = (".py",)

    #: Rule-specific config (instance attrs; from_config overrides per consumer).
    markers: tuple[str, ...] = DEFAULT_MARKERS
    header_lines: int = DEFAULT_HEADER_LINES

    @classmethod
    def from_config(
        cls,
        config: Mapping[str, Any],
        *,
        repo_root: Path | None = None,
    ) -> LicensePresent:
        rule = super().from_config(config, repo_root=repo_root)
        assert isinstance(rule, LicensePresent)  # noqa: S101  # narrowing for mypy
        rule.markers = tuple(config.get("markers", DEFAULT_MARKERS))
        rule.header_lines = int(config.get("header_lines", DEFAULT_HEADER_LINES))
        return rule

    def file_has_violation(self, path: Path) -> bool:
        return file_missing_license(path, markers=self.markers, header_lines=self.header_lines)


def build(
    config: Mapping[str, Any],
    *,
    repo_root: Path | None = None,
) -> LicensePresent:
    """Factory the engine calls to bind this CORE check to a consumer's config."""
    return LicensePresent.from_config(config, repo_root=repo_root)


def main(argv: list[str] | None = None) -> int:
    """CLI entry — supports ``--establish-baseline`` and ``--repo-root``."""
    return run_core_check(LicensePresent, argv)


if __name__ == "__main__":
    import sys

    sys.exit(main())
