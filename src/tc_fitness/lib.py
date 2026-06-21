"""Merged shared helpers for architecture-fitness checks across Three Cubes repos.

This module unions two independently-grown libraries into one source:

- **kairix** ``scripts/checks/_arch_lib.py`` — baseline-gating helpers:
  :func:`gate`, :func:`python_files`, :func:`main_entry`, :func:`repo_relative`,
  and the :data:`REPO_ROOT` anchor. Each check reports a set of offending
  paths and compares against ``.architecture/baseline/<name>-files.txt``; net-new
  violations exit non-zero, baseline files are grandfathered.

- **tc-agent-zone** ``scripts/checks/_lib/__init__.py`` — agent-actionable
  emit/YAML helpers: :func:`actionable`, :func:`emit_failures`, :func:`emit_pass`,
  :func:`load_yaml`, :func:`missing_keys`. These shape FAIL/PASS output per the
  canonical ``<what>; fix: <fix>; next: <nxt>`` form and load YAML with a
  ``(data, error)`` contract.

Both call patterns are preserved exactly so the ~80 kairix checks and ~95
tc-agent-zone checks can adopt this package without rewriting their call sites.

REPO_ROOT note
--------------
The original kairix module derived ``REPO_ROOT`` from its own file location
(``parent.parent.parent``). Inside an installed package that anchor is wrong,
so :data:`REPO_ROOT` here resolves from the current working directory, which is
the repo root when checks run from ``scripts/safe-commit.sh`` / pre-commit / CI.
Every gating helper also accepts an explicit ``repo_root`` argument; callers that
need isolation (tests, monorepo sub-trees) pass it directly rather than relying
on the default.
"""

from __future__ import annotations

import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# kairix _arch_lib surface — baseline gating
# ---------------------------------------------------------------------------

# Anchored to CWD so an installed package gates the *consumer* repo, not the
# site-packages tree. Checks run from the repo root, so this is correct in the
# pre-commit / safe-commit / CI invocation paths. Pass repo_root= explicitly
# anywhere that assumption does not hold.
REPO_ROOT = Path.cwd()

_RED = "\033[0;31m"
_GREEN = "\033[0;32m"
_YELLOW = "\033[0;33m"
_RESET = "\033[0m"


def _baseline_dir(repo_root: Path) -> Path:
    return repo_root / ".architecture" / "baseline"


def _print_stale_failure(
    name: str,
    stale: list[object],
    stale_remediation: str | None,
    *,
    kind: str,
) -> None:
    """Print the canonical STALE-baseline FAIL block for ``gate`` / ``gate_keys``.

    Single-sourced so both gates emit identical framing; the consumer supplies
    the remediation text (no engine-baked wording)."""
    print(f"{_RED}FAIL [arch:{name}]{_RESET} — stale baseline {kind}(s) no longer in the current scan:")
    for s in stale:
        print(f"  {s}: STALE — remove this line from the baseline.")
    if stale_remediation:
        print()
        print(stale_remediation)


def _print_pass_counts(name: str, *, new_count: int, grandfathered: int) -> None:
    """Print the pass banner with new-vs-grandfathered counts (v0.4.0)."""
    if grandfathered > 0:
        print(
            f"{_YELLOW}ok [arch:{name}]{_RESET} — {new_count} new, "
            f"{grandfathered} grandfathered (still present in baseline)."
        )
    else:
        print(f"{_GREEN}ok [arch:{name}]{_RESET} — clean ({new_count} new, 0 grandfathered).")


def gate(
    name: str,
    current: set[Path],
    remediation: str,
    *,
    repo_root: Path | None = None,
    fail_on_stale: bool = False,
    stale_remediation: str | None = None,
) -> int:
    """Compare current violations against the baseline; print + return exit code.

    Args:
        name: short rule name (used in messages and baseline filename).
        current: set of repo-relative (or absolute under ``repo_root``) Paths
            with the violation.
        remediation: operator-actionable remediation hint.
        repo_root: repo root to resolve the baseline against and to relativise
            absolute paths. Defaults to :data:`REPO_ROOT` (the CWD).
        fail_on_stale: when ``True`` (v0.4.0), a baseline entry that no longer
            appears in ``current`` is STALE and FAILs the gate (the consumer
            supplies ``stale_remediation``). Default ``False`` preserves the
            v0.1.0 "shrinks are clean" exit-code contract byte-identically.
        stale_remediation: the operator-actionable text printed under a stale
            FAIL. Single-sourced from the consumer — no engine-baked wording.

    Returns:
        ``0`` if no NEW violations (and, when ``fail_on_stale``, no stale
        entries); ``1`` if NEW violations were introduced OR a stale baseline
        entry was found.
    """
    root = repo_root if repo_root is not None else REPO_ROOT
    baseline_file = _baseline_dir(root) / f"{name}-files.txt"
    if baseline_file.exists():
        baseline = {
            Path(line.strip())
            for line in baseline_file.read_text().splitlines()
            if line.strip() and not line.startswith("#")
        }
    else:
        baseline = set()

    current_rel = {p.relative_to(root) if p.is_absolute() else p for p in current}
    new = sorted(current_rel - baseline)

    if new:
        print(f"{_RED}FAIL [arch:{name}]{_RESET} — new violation(s) introduced:")
        for p in new:
            print(f"  {p}")
        print()
        print(remediation)
        print()
        try:
            baseline_rel = baseline_file.relative_to(root)
        except ValueError:
            baseline_rel = baseline_file
        print(
            "If this is genuinely the only practical fix, document why in the\n"
            f"PR description and append the file to {baseline_rel}\n"
            "(but expect pushback at review time — adding to the baseline is rare)."
        )
        return 1

    if fail_on_stale:
        stale = sorted(baseline - current_rel)
        if stale:
            _print_stale_failure(name, list(stale), stale_remediation, kind="file")
            return 1
        _print_pass_counts(name, new_count=0, grandfathered=len(baseline))
        return 0

    # Default path: byte-identical to v0.1.0 (no counts banner change).
    remaining = len(baseline)
    if remaining > 0:
        print(f"{_YELLOW}ok [arch:{name}]{_RESET} — {remaining} grandfathered file(s) still present in baseline.")
    else:
        print(f"{_GREEN}ok [arch:{name}]{_RESET} — clean.")
    return 0


def gate_keys(
    name: str,
    current: set[str],
    remediation: str,
    *,
    repo_root: Path | None = None,
    baseline_suffix: str = "-ids.txt",
    fail_on_stale: bool = False,
    stale_remediation: str | None = None,
) -> int:
    """Ratchet an arbitrary set of *string keys* against a baseline file.

    The string-keyed sibling of :func:`gate`. Where :func:`gate` keys on
    working-tree :class:`~pathlib.Path` objects (relativising absolute paths
    under ``repo_root``), this keys on opaque string identifiers — logical rule
    ids (``F30``), path-globs (``kairix/**/web/static/*``), or any other token
    that is NOT a real file path. Net-new keys fail; baseline keys are
    grandfathered; the set shrinking is always clean. Same exit-code contract as
    :func:`gate`.

    tc-agent-zone has 13 checks whose baseline KEY is a logical id (``-ids.txt``)
    or a path-glob (``-paths.txt``) rather than a working-tree file path; those
    checks ratchet through this helper.

    Args:
        name: short rule name (used in messages and baseline filename).
        current: set of string keys currently in violation.
        remediation: operator-actionable remediation hint.
        repo_root: repo root to resolve the baseline against. Defaults to
            :data:`REPO_ROOT` (the CWD).
        baseline_suffix: filename suffix for the baseline file, so a check can
            select ``-ids.txt`` (logical ids, the default) or ``-paths.txt``
            (path-globs) per its key kind. The baseline is read from
            ``.architecture/baseline/<name><baseline_suffix>``.
        fail_on_stale: when ``True`` (v0.4.0), a baseline key no longer in
            ``current`` is STALE and FAILs (the consumer supplies
            ``stale_remediation``). Default ``False`` keeps the shrinks-are-clean
            contract byte-identically.
        stale_remediation: the text printed under a stale FAIL.

    Returns:
        ``0`` if no NEW keys (and, when ``fail_on_stale``, no stale keys);
        ``1`` if NEW keys were introduced OR a stale baseline key was found.
    """
    root = repo_root if repo_root is not None else REPO_ROOT
    baseline_file = _baseline_dir(root) / f"{name}{baseline_suffix}"
    if baseline_file.exists():
        baseline = {
            line.strip()
            for line in baseline_file.read_text().splitlines()
            if line.strip() and not line.startswith("#")
        }
    else:
        baseline = set()

    new = sorted(current - baseline)

    if new:
        print(f"{_RED}FAIL [arch:{name}]{_RESET} — new violation(s) introduced:")
        for key in new:
            print(f"  {key}")
        print()
        print(remediation)
        print()
        try:
            baseline_rel = baseline_file.relative_to(root)
        except ValueError:
            baseline_rel = baseline_file
        print(
            "If this is genuinely the only practical fix, document why in the\n"
            f"PR description and append the key to {baseline_rel}\n"
            "(but expect pushback at review time — adding to the baseline is rare)."
        )
        return 1

    if fail_on_stale:
        stale = sorted(baseline - current)
        if stale:
            _print_stale_failure(name, list(stale), stale_remediation, kind="key")
            return 1
        _print_pass_counts(name, new_count=0, grandfathered=len(baseline))
        return 0

    # Default path: byte-identical to v0.2.0 (no counts banner change).
    remaining = len(baseline)
    if remaining > 0:
        print(f"{_YELLOW}ok [arch:{name}]{_RESET} — {remaining} grandfathered key(s) still present in baseline.")
    else:
        print(f"{_GREEN}ok [arch:{name}]{_RESET} — clean.")
    return 0


def repo_relative(path: Path, *, repo_root: Path | None = None) -> Path:
    """Convert an absolute path under the repo root to a repo-relative Path."""
    root = repo_root if repo_root is not None else REPO_ROOT
    return path.resolve().relative_to(root)


def python_files(*roots: str, repo_root: Path | None = None) -> list[Path]:
    """Return all ``.py`` files under the given relative roots, skipping ``__pycache__``."""
    root = repo_root if repo_root is not None else REPO_ROOT
    out: list[Path] = []
    for rel in roots:
        root_path = root / rel
        if not root_path.exists():
            continue
        for p in root_path.rglob("*.py"):
            if "__pycache__" in p.parts:
                continue
            out.append(p)
    return out


def main_entry(
    check_fn: Callable[[Path], object] | object,
    name: str,
    remediation: str,
    *roots: str,
    repo_root: Path | None = None,
) -> int:
    """Scan ``roots``, call ``check_fn(path)`` on each ``.py`` file, gate on the union.

    ``check_fn`` returns either ``True`` (file has a violation) or a falsy value.
    """
    root = repo_root if repo_root is not None else REPO_ROOT
    violations: set[Path] = set()
    for path in python_files(*roots, repo_root=root):
        if callable(check_fn) and check_fn(path):
            violations.add(repo_relative(path, repo_root=root))
    return gate(name, violations, remediation, repo_root=root)


# ---------------------------------------------------------------------------
# tc-agent-zone _lib surface — agent-actionable emit / YAML
# ---------------------------------------------------------------------------


def actionable(what: str, fix: str, nxt: str, run: str | None = None) -> str:
    """Format an agent-actionable single-line failure.

    Shape (default): ``<what>; fix: <fix>; next: <nxt>``. Standardising the shape
    lets the actionable-feedback parser keep up without chasing each call site's
    bespoke formatting.

    When ``run`` is supplied, a third ``; run: <run>`` marker is appended,
    yielding the 3-marker ``<what>; fix: <fix>; next: <nxt>; run: <run>`` form
    that tc-agent-zone's 59 fix/next/run checks emit. ``run`` defaults to
    ``None``, so the 2-marker v0.1.0 output is byte-identical for every existing
    call site.
    """
    base = f"{what}; fix: {fix}; next: {nxt}"
    if run is None:
        return base
    return f"{base}; run: {run}"


def remediation(
    fix: str,
    nxt: str,
    run: str,
    *,
    passing: str | None = None,
    forbidden: str | None = None,
) -> str:
    """Format the multiline ``fix:`` / ``next:`` / ``run:`` remediation block.

    The F21-shape block tc-agent-zone's 30 checks emit alongside a failure: the
    three action markers on their own lines, optionally followed by a ``Pass``
    example and a ``Forbidden`` example. Returns the joined block as one string
    (no trailing newline), ready to ``print()``.

    Example output::

        fix: redact the secret before logging
        next: re-run the check
        run: python scripts/checks/check_f15.py
        Pass: logger.info("token redacted")
        Forbidden: logger.info(f"token={token}")

    Args:
        fix: the corrective action.
        nxt: the follow-up step after fixing.
        run: the exact command to re-verify.
        passing: optional Pass-example line (omitted when ``None``).
        forbidden: optional Forbidden-example line (omitted when ``None``).
    """
    lines = [f"fix: {fix}", f"next: {nxt}", f"run: {run}"]
    if passing is not None:
        lines.append(f"Pass: {passing}")
    if forbidden is not None:
        lines.append(f"Forbidden: {forbidden}")
    return "\n".join(lines)


def emit_failures(check_name: str, fails: list[str], stream: Any = None) -> None:
    """Emit the canonical FAIL banner + bulleted failure list.

    Defaults to ``sys.stderr`` (resolved at call time so tests can capture it).
    """
    out = stream if stream is not None else sys.stderr
    print(f"FAIL {check_name} ({len(fails)} violations)", file=out)
    for f in fails:
        print(f"  - {f}", file=out)


def emit_pass(message: str, stream: Any = None) -> None:
    """Emit the canonical PASS line for a check (defaults to ``sys.stdout``)."""
    out = stream if stream is not None else sys.stdout
    print(message, file=out)


def load_yaml(path: Path) -> tuple[Any, str | None]:
    """Load YAML returning ``(data, error)``.

    Returns ``({} or scalar, None)`` on success; ``(None, error-str)`` on a
    missing PyYAML dependency or a parse failure. Callers decide whether the
    error is fatal. PyYAML is imported lazily so consumers that never call this
    helper need not install the ``yaml`` extra.
    """
    try:
        import yaml
    except ImportError:
        return None, "PyYAML missing"
    try:
        return yaml.safe_load(path.read_text()) or {}, None
    except yaml.YAMLError as e:
        return None, f"invalid YAML — {e}"


def missing_keys(parsed: dict[str, Any], required: tuple[str, ...]) -> list[str]:
    """Return the subset of ``required`` keys that are absent in ``parsed``."""
    return [k for k in required if k not in parsed]


__all__ = [
    "REPO_ROOT",
    "actionable",
    "emit_failures",
    "emit_pass",
    "gate",
    "gate_keys",
    "load_yaml",
    "main_entry",
    "missing_keys",
    "python_files",
    "remediation",
    "repo_relative",
]
