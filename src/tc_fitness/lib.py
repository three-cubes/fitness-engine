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
from pathlib import Path
from typing import Any, Callable

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


def gate(
    name: str,
    current: set[Path],
    remediation: str,
    *,
    repo_root: Path | None = None,
) -> int:
    """Compare current violations against the baseline; print + return exit code.

    Args:
        name: short rule name (used in messages and baseline filename).
        current: set of repo-relative (or absolute under ``repo_root``) Paths
            with the violation.
        remediation: operator-actionable remediation hint.
        repo_root: repo root to resolve the baseline against and to relativise
            absolute paths. Defaults to :data:`REPO_ROOT` (the CWD).

    Returns:
        ``0`` if no NEW violations (baseline matches or shrinks); ``1`` if NEW
        violations were introduced.
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

    remaining = len(baseline)
    if remaining > 0:
        print(f"{_YELLOW}ok [arch:{name}]{_RESET} — {remaining} grandfathered file(s) still present in baseline.")
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


def actionable(what: str, fix: str, nxt: str) -> str:
    """Format an agent-actionable single-line failure.

    Shape: ``<what>; fix: <fix>; next: <nxt>``. Standardising the shape lets the
    actionable-feedback parser keep up without chasing each call site's bespoke
    formatting.
    """
    return f"{what}; fix: {fix}; next: {nxt}"


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


def missing_keys(parsed: dict, required: tuple[str, ...]) -> list[str]:
    """Return the subset of ``required`` keys that are absent in ``parsed``."""
    return [k for k in required if k not in parsed]


__all__ = [
    "REPO_ROOT",
    "gate",
    "repo_relative",
    "python_files",
    "main_entry",
    "actionable",
    "emit_failures",
    "emit_pass",
    "load_yaml",
    "missing_keys",
]
