"""Per-file baseline I/O — the canonical grandfathered-offender ledger (v0.6.0).

A *baseline* is a newline-delimited list of grandfathered offenders that a
fitness rule MUST tolerate (they pre-date the rule's introduction) while it
FAILS on anything net-new. The file lives at::

    .architecture/baseline/<canonical-check-name>-files.txt

One canonical suffix — ``-files.txt`` — for the per-file baseline a
:class:`tc_fitness.fitness_rule.FitnessRule` reads. (The string-keyed sibling
:func:`tc_fitness.gate_keys` keeps its own ``-ids.txt`` / ``-paths.txt`` suffix
selection; this module is the *file-path* baseline that the FitnessRule ABC
loads.)

Repo-agnostic
-------------
Nothing here knows about kairix's ``f44`` or tc-agent-zone's
``no-duplicate-string``. The canonical check name and the repo root are the
two inputs; the path is derived. The leading-comment block written by
:func:`establish_baseline` carries no repo identity beyond the check name the
caller passes.

The ``--establish-baseline`` mode
---------------------------------
Adoption of a new rule never breaks the build: a consumer runs the rule's
detector once in establish mode, which writes *today's* offenders to the
baseline (with a mandatory leading comment block explaining what the file is
and that it may only SHRINK). From then on the rule FAILS only on net-new
violations vs that frozen set. :func:`establish_baseline` is the single writer;
:func:`load_baseline` is the single reader; both agree on the parse contract
(skip blank lines and ``#``-prefixed comments, strip trailing whitespace).
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path

#: The one canonical per-file baseline suffix. A check named ``no-duplicate-string``
#: reads ``.architecture/baseline/no-duplicate-string-files.txt``.
BASELINE_SUFFIX = "-files.txt"

#: Repo-relative directory every baseline lives under.
BASELINE_DIRNAME = Path(".architecture") / "baseline"


def baseline_dir(repo_root: Path) -> Path:
    """The ``.architecture/baseline`` directory under ``repo_root``."""
    return repo_root / BASELINE_DIRNAME


def baseline_path(name: str, repo_root: Path) -> Path:
    """Resolve the per-file baseline path for canonical check ``name``.

    ``name`` is the canonical check name (``"no-duplicate-string"``,
    ``"f44"``); the ``-files.txt`` suffix and the ``.architecture/baseline``
    directory are appended. Repo-agnostic — only ``name`` and ``repo_root``
    are inputs.
    """
    return baseline_dir(repo_root) / f"{name}{BASELINE_SUFFIX}"


def parse_baseline_text(text: str) -> set[str]:
    """Parse baseline file *content* into a set of entries.

    The single parse contract shared by the reader and the
    ``--establish-baseline`` writer: skip blank lines and ``#``-prefixed
    comment lines; strip surrounding whitespace from each retained entry
    (defends against editor-introduced trailing whitespace).
    """
    out: set[str] = set()
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        out.add(stripped)
    return out


def load_baseline(name: str, repo_root: Path) -> set[str]:
    """Load the grandfathered entries for canonical check ``name``.

    Returns the empty set when the baseline file does not exist (a brand-new
    rule with no baseline yet grandfathers nothing — every current violation
    is net-new). Uses :func:`parse_baseline_text` so the reader and writer
    never diverge on the comment / blank-line contract.
    """
    path = baseline_path(name, repo_root)
    if not path.exists():
        return set()
    return parse_baseline_text(path.read_text(encoding="utf-8"))


def _header_block(name: str) -> list[str]:
    """The mandatory leading comment block for an established baseline.

    Repo-agnostic: carries the check name (so a reader knows which rule the
    file grandfathers) and the SHRINK-ONLY contract (so no one re-purposes
    the file as an allow-list that grows). No repo identity beyond ``name``.
    """
    stamp = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    return [
        f"# Baseline for fitness check: {name}",
        "#",
        "# Grandfathered offenders that pre-date this rule. The rule FAILS only",
        "# on NET-NEW violations vs this list — pre-existing entries are tolerated.",
        "#",
        "# This file may only SHRINK. Pay an entry down (fix the underlying",
        "# violation, then delete its line) — never append a net-new offender.",
        f"# Established {stamp} via --establish-baseline.",
        "#",
        "# One repo-relative path per line. Blank lines and '#' comments ignored.",
    ]


def render_baseline(name: str, entries: Iterable[str]) -> str:
    """Render the full baseline file text (header block + sorted entries).

    Pure function (no I/O) so callers and tests can assert on the exact bytes.
    Entries are de-duplicated and sorted for a stable, diff-friendly ordering.
    A trailing newline terminates the file.
    """
    lines = _header_block(name)
    unique = sorted(set(entries))
    if unique:
        lines.append("")
        lines.extend(unique)
    return "\n".join(lines) + "\n"


def establish_baseline(
    name: str,
    entries: Iterable[str],
    repo_root: Path,
) -> Path:
    """Write today's offenders as the frozen baseline for check ``name``.

    The ``--establish-baseline`` mode: a consumer adopting a new rule runs its
    detector once and feeds the offending repo-relative paths here. The
    ``.architecture/baseline`` directory is created if absent. Returns the path
    written so callers can report it. Overwrites any existing baseline (the
    operator is deliberately re-freezing).
    """
    path = baseline_path(name, repo_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_baseline(name, entries), encoding="utf-8")
    return path


__all__ = [
    "BASELINE_SUFFIX",
    "BASELINE_DIRNAME",
    "baseline_dir",
    "baseline_path",
    "parse_baseline_text",
    "load_baseline",
    "render_baseline",
    "establish_baseline",
]
