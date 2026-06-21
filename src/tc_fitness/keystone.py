"""Keystone drift-enders — the checks that make every per-file baseline a ratchet.

Three engine-CORE invariants, repo-agnostic, that together guarantee a baseline
can only ever SHRINK and a catalogue can never silently lie:

* :func:`net_new_violations_forbidden` (lifted from kairix F50) — a file ADDED
  in the current change cannot appear in any per-file baseline. Closes the
  loophole that per-file shrink-only baselines leave open: a brand-new file
  could otherwise land already-grandfathered. New files MUST land clean.

* :func:`baseline_shrink_only` (lifted from kairix F49) — across a release
  boundary every governed baseline must DROP at least one entry or stay at
  zero; it may never grow or stall above zero. THE drift-ender: debt only ever
  pays down.

* :func:`catalogue_check_consistency` (lifted from kairix F92) — every
  ``RuleEntry`` in a consumer's catalogue resolves to a real check, AND every
  check the consumer ships is cataloged. Bidirectional: no orphan checks, no
  dangling entries.

All three are CONFIG-DRIVEN. The consumer supplies the baseline directory, the
governed-rule set, the previous-tag resolver, and the catalogue ↔ checks
resolvers. Nothing here hardcodes a repo's paths, rule ids, or tag glob.
"""

from __future__ import annotations

import subprocess
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from tc_fitness.baseline import BASELINE_SUFFIX, baseline_dir, parse_baseline_text

# ===========================================================================
# net_new_violations_forbidden  (kairix F50)
# ===========================================================================


def load_all_baselines(repo_root: Path) -> dict[str, set[str]]:
    """``{baseline_filename: {entries...}}`` for every ``*-files.txt`` baseline.

    Repo-agnostic: walks ``.architecture/baseline/*-files.txt`` under
    ``repo_root`` and parses each with the canonical
    :func:`tc_fitness.baseline.parse_baseline_text` contract.
    """
    out: dict[str, set[str]] = {}
    bdir = baseline_dir(repo_root)
    if not bdir.is_dir():
        return out
    for path in sorted(bdir.glob(f"*{BASELINE_SUFFIX}")):
        out[path.name] = parse_baseline_text(path.read_text(encoding="utf-8"))
    return out


def find_net_new_violations(
    added_files: Iterable[str],
    baselines: Mapping[str, set[str]],
) -> dict[str, list[str]]:
    """``{baseline_filename: [violating_added_paths]}`` for any added∩baseline hit.

    Empty dict when no added file appears in any baseline. Pure function — the
    set of added files and the baseline map are both injected.
    """
    added_set = set(added_files)
    out: dict[str, list[str]] = {}
    for name, entries in baselines.items():
        hits = sorted(added_set & entries)
        if hits:
            out[name] = hits
    return out


def net_new_violations_forbidden(
    added_files: Iterable[str],
    repo_root: Path,
    *,
    remediation: str = "",
    print_fn: Callable[[str], None] = print,
) -> int:
    """Gate: no ADDED file may appear in any per-file baseline.

    ``added_files`` is the set of repo-relative paths added by the change under
    test (the consumer resolves them — staged diff for pre-commit, tag-diff for
    CI; see :func:`staged_added_files` / :func:`added_since_tag`). Returns ``0``
    when clean, ``1`` when an added file is grandfathered. The consumer supplies
    ``remediation`` (no engine-baked wording).
    """
    baselines = load_all_baselines(repo_root)
    violations = find_net_new_violations(added_files, baselines)
    if not violations:
        return 0
    print_fn("FAIL net_new_violations_forbidden — added file(s) already grandfathered:")
    for name in sorted(violations):
        print_fn(f"  baseline {name}:")
        for path in violations[name]:
            print_fn(f"    {path}")
    if remediation:
        print_fn("")
        print_fn(remediation)
    return 1


def staged_added_files(repo_root: Path) -> list[str]:
    """Repo-relative paths ADDED in the current staged diff (pre-commit mode)."""
    result = subprocess.run(
        ["git", "diff", "--cached", "--name-only", "--diff-filter=A"],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        return []
    return [line for line in result.stdout.splitlines() if line.strip()]


def added_since_tag(repo_root: Path, tag: str) -> list[str]:
    """Repo-relative paths ADDED between ``tag`` and HEAD (CI full-PR mode)."""
    result = subprocess.run(
        ["git", "diff", "--name-only", "--diff-filter=A", f"{tag}..HEAD"],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        return []
    return [line for line in result.stdout.splitlines() if line.strip()]


# ===========================================================================
# baseline_shrink_only  (kairix F49)
# ===========================================================================


def _count_entries(text: str) -> int:
    return len(parse_baseline_text(text))


def _read_head(repo_root: Path, rel_path: str) -> str:
    path = repo_root / rel_path
    return path.read_text(encoding="utf-8") if path.exists() else ""


def _read_at_ref(repo_root: Path, rel_path: str, ref: str) -> str:
    result = subprocess.run(
        ["git", "show", f"{ref}:{rel_path}"],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=False,
    )
    return result.stdout if result.returncode == 0 else ""


def resolve_previous_tag(
    repo_root: Path,
    *,
    match_glob: str = "v[0-9]*.[0-9]*.[0-9]*",
) -> str | None:
    """Most recent release tag strictly older than HEAD, or ``None``.

    ``match_glob`` is CONFIG — a repo using ``v2026.5.18`` and a repo using
    ``v0.6.0`` both work by passing their own glob. ``None`` means "no prior
    tag" (first release → the shrink check is a clean skip).
    """
    result = subprocess.run(
        ["git", "describe", "--tags", "--abbrev=0", "--match", match_glob, "HEAD^"],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        return None
    return result.stdout.strip() or None


@dataclass(frozen=True)
class ShrinkResult:
    """Per-baseline shrink verdict for the release-boundary check."""

    rel_path: str
    prev_count: int
    head_count: int

    @property
    def ok(self) -> bool:
        """OK iff the baseline shrank or stayed at zero."""
        if self.head_count == 0:
            return True
        return self.head_count < self.prev_count


def baseline_shrink_only(
    governed_baseline_paths: Sequence[str],
    repo_root: Path,
    *,
    prev_tag: str | None = None,
    match_glob: str = "v[0-9]*.[0-9]*.[0-9]*",
    remediation: str = "",
    print_fn: Callable[[str], None] = print,
) -> int:
    """Gate: every governed baseline shrank (or stayed at zero) since ``prev_tag``.

    ``governed_baseline_paths`` is the CONFIG list of repo-relative baseline
    files the consumer governs (typically DERIVED from its catalogue so a
    rename can't silently make the check vacuous). ``prev_tag`` is auto-resolved
    via :func:`resolve_previous_tag` when ``None``. First release (no prior tag)
    → clean skip. Returns ``0`` when every governed baseline is OK, ``1``
    otherwise.
    """
    if prev_tag is None:
        prev_tag = resolve_previous_tag(repo_root, match_glob=match_glob)
    if prev_tag is None:
        print_fn("baseline_shrink_only: no previous release tag — first release, skipping.")
        return 0

    results: list[ShrinkResult] = []
    for rel_path in governed_baseline_paths:
        head = _count_entries(_read_head(repo_root, rel_path))
        prev = _count_entries(_read_at_ref(repo_root, rel_path, prev_tag))
        results.append(ShrinkResult(rel_path, prev, head))

    failures = [r for r in results if not r.ok]
    if not failures:
        print_fn(f"baseline_shrink_only: all {len(results)} governed baseline(s) shrank or stayed at zero.")
        return 0

    print_fn(f"FAIL baseline_shrink_only — baseline(s) did not shrink since {prev_tag}:")
    for r in failures:
        verb = "grew" if r.head_count > r.prev_count else "did not shrink"
        print_fn(f"  {r.rel_path}: prev={r.prev_count} head={r.head_count} ({verb})")
    if remediation:
        print_fn("")
        print_fn(remediation)
    return 1


# ===========================================================================
# catalogue_check_consistency  (kairix F92, invariants a + b)
# ===========================================================================


@dataclass(frozen=True)
class CatalogueConsistencyReport:
    """Bidirectional catalogue ↔ checks reconciliation result."""

    orphan_checks: list[str]
    dangling_entries: list[tuple[str, str]]

    @property
    def ok(self) -> bool:
        return not self.orphan_checks and not self.dangling_entries


def reconcile_catalogue(
    *,
    cataloged_check_ids: Iterable[str],
    available_check_ids: Iterable[str],
) -> CatalogueConsistencyReport:
    """Reconcile the cataloged check ids against the checks that actually exist.

    * **orphan_checks** — a check the consumer ships that NO catalogue entry
      references (it runs but nothing documents what it protects).
    * **dangling_entries** — a catalogue entry referencing a check that does
      NOT exist (the runner would mis-dispatch).

    Both id sets are CONFIG: the consumer resolves "what does my catalogue
    reference" and "what checks exist" however its layout dictates (CORE
    ``core:<module>`` ids, local ``check_*.py`` filenames, …). The engine only
    diffs the two sets — fully repo-agnostic.
    """
    cataloged = set(cataloged_check_ids)
    available = set(available_check_ids)
    orphans = sorted(available - cataloged)
    dangling = sorted((cid, cid) for cid in (cataloged - available))
    return CatalogueConsistencyReport(
        orphan_checks=orphans,
        dangling_entries=[(eid, cid) for eid, cid in dangling],
    )


def catalogue_check_consistency(
    *,
    cataloged_check_ids: Iterable[str],
    available_check_ids: Iterable[str],
    remediation: str = "",
    print_fn: Callable[[str], None] = print,
) -> int:
    """Gate: catalogue ↔ checks agree bidirectionally. ``0`` ok, ``1`` drift.

    Thin printing wrapper over :func:`reconcile_catalogue`. The consumer
    supplies both id sets and the ``remediation`` text.
    """
    report = reconcile_catalogue(
        cataloged_check_ids=cataloged_check_ids,
        available_check_ids=available_check_ids,
    )
    if report.ok:
        print_fn("catalogue_check_consistency: catalogue and checks agree.")
        return 0
    if report.orphan_checks:
        print_fn("FAIL catalogue_check_consistency — check(s) with no catalogue entry:")
        for name in report.orphan_checks:
            print_fn(f"  {name}")
    if report.dangling_entries:
        print_fn("FAIL catalogue_check_consistency — catalogue entr(ies) naming a missing check:")
        for entry_id, check_id in report.dangling_entries:
            print_fn(f"  {entry_id} -> {check_id}")
    if remediation:
        print_fn("")
        print_fn(remediation)
    return 1


__all__ = [
    # net-new
    "load_all_baselines",
    "find_net_new_violations",
    "net_new_violations_forbidden",
    "staged_added_files",
    "added_since_tag",
    # shrink-only
    "ShrinkResult",
    "resolve_previous_tag",
    "baseline_shrink_only",
    # catalogue consistency
    "CatalogueConsistencyReport",
    "reconcile_catalogue",
    "catalogue_check_consistency",
]
