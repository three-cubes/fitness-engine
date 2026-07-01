"""CORE check: engine_version_floor — the pinned engine version clears a floor.

A consumer repo pins the shared fitness engine (``three-cubes-fitness``) to an
exact tag — typically a git ref like
``three-cubes-fitness @ git+https://.../tc-fitness.git@vX.Y.Z``, a git-URL
dependency the uv ecosystem cannot range-track — and over time consumers drift:
one repo sits a minor version behind the gate-critical floor while another rides
the head, so a check that was fixed centrally quietly never runs in the laggard.
This rule reads the consuming repo's OWN pinned engine tag and FAILS when it is
BELOW a centrally-declared floor, surfacing the lag at the gate.

Guard-forward (decision D2): the floor is CONSUMER config
(``[tool.tc_fitness.core_checks.engine_version_floor] floor = "vX.Y.Z"``), not a
constant baked into the engine — a repo with no floor configured is a NO-OP, so
adopting the check never hard-breaks a consumer before its own bump PR lands.
The floor ships set to the current MINIMUM pinned tag across the fleet with a
ratchet note: it may only rise.

Repo-agnostic: the only literal this module carries is the engine's OWN
distribution name (overridable via the ``package`` config key) — it names no
consuming repo. Version resolution prefers the consumer's DECLARED pin (its
``pyproject.toml`` dependency spec or uv source tag) and falls back to the
installed distribution's metadata.
"""

from __future__ import annotations

import re
import tomllib
from collections.abc import Iterator, Mapping
from importlib import metadata as _metadata
from pathlib import Path
from typing import Any

from tc_fitness.core_checks import run_core_check
from tc_fitness.fitness_rule import FitnessRule
from tc_fitness.lib import remediation as _remediation

#: The shared engine's own distribution name — the dependency a consumer pins.
#: Overridable via the ``package`` config key; it names the engine, not a repo.
DEFAULT_PACKAGE = "three-cubes-fitness"

#: The consumer manifest read for the declared pin, relative to the repo root.
_PYPROJECT = "pyproject.toml"

#: A dotted numeric release, optionally ``v``-prefixed (``v0.6.1`` → ``0.6.1``).
_RELEASE_RE = re.compile(r"v?(\d+(?:\.\d+)*)")

#: A PEP 508 version specifier carrying an exact-or-lower pin (``name==X.Y.Z``).
_SPECIFIER_RE = re.compile(r"^([A-Za-z0-9][A-Za-z0-9._-]*)(?:\[[^\]]*\])?\s*(?:===|==|~=|>=)\s*([^,;\s]+)")

REMEDIATION = _remediation(
    fix=(
        "bump the pinned engine dependency to at least the configured floor — "
        "update the engine tag in pyproject.toml (its git-URL `@vX.Y.Z` ref or "
        "its uv source `tag`) to a version at or above the floor and re-lock. "
        "The floor is a control-plane ratchet; lowering it is a CODEOWNERS-gated "
        "edit, not a fix."
    ),
    nxt="re-run this check to confirm the pin clears the floor.",
    run="python -m tc_fitness.core_checks.engine_version_floor",
    passing="pinned @v0.7.0 with floor v0.7.0",
    forbidden="pinned @v0.6.1 with floor v0.7.0",
)


def parse_version(text: str) -> tuple[int, ...] | None:
    """Parse ``vX.Y.Z`` / ``X.Y.Z`` into a comparable release tuple, else ``None``.

    Only the leading dotted-numeric release is read (any pre-release / local
    suffix is ignored), so ``v0.6.1`` → ``(0, 6, 1)``. A string with no leading
    numeric release (a branch name, a bare URL) yields ``None`` — the caller
    treats an unparseable version as "cannot determine", never as a violation.
    """
    match = _RELEASE_RE.match(text.strip())
    if match is None:
        return None
    return tuple(int(part) for part in match.group(1).split("."))


def _normalize(name: str) -> str:
    """PEP 503 name normalisation so ``Three_Cubes.Fitness`` matches its package."""
    return re.sub(r"[-_.]+", "-", name).strip().lower()


def _pin_from_spec(spec: str, package: str) -> str | None:
    """Extract the pinned version token from one dependency spec, or ``None``.

    Handles a PEP 508 direct-URL reference (``name @ git+...@<tag>`` — the tag is
    the segment after the URL's final ``@``, minus any ``#`` fragment) and an
    exact/compatible version specifier (``name==X.Y.Z``). A spec for a different
    package yields ``None``.
    """
    spec = spec.strip()
    if "://" in spec:
        name_part, _, ref = spec.partition("@")
        if _normalize(name_part.split("[", 1)[0]) != _normalize(package):
            return None
        url = ref.split("#", 1)[0].strip()
        _, at, tag = url.rpartition("@")
        return tag if at and tag else None
    match = _SPECIFIER_RE.match(spec)
    if match is None or _normalize(match.group(1)) != _normalize(package):
        return None
    return match.group(2)


def _iter_dependency_specs(data: Mapping[str, Any]) -> Iterator[str]:
    """Yield every string dependency spec across the manifest's dependency tables.

    Covers ``[project.dependencies]``, each ``[project.optional-dependencies]``
    group, and each PEP 735 ``[dependency-groups]`` group. Non-string entries (a
    dependency-group ``include-group`` table) are skipped.
    """
    project = data.get("project", {})
    if isinstance(project, Mapping):
        for dep in project.get("dependencies", []) or []:
            if isinstance(dep, str):
                yield dep
        optional = project.get("optional-dependencies", {})
        if isinstance(optional, Mapping):
            for group in optional.values():
                for dep in group or []:
                    if isinstance(dep, str):
                        yield dep
    groups = data.get("dependency-groups", {})
    if isinstance(groups, Mapping):
        for group in groups.values():
            if isinstance(group, list):
                for dep in group:
                    if isinstance(dep, str):
                        yield dep


def _uv_source_tag(data: Mapping[str, Any], package: str) -> str | None:
    """The ``tag`` a ``[tool.uv.sources]`` entry pins ``package`` to, or ``None``.

    When a consumer uses uv sources, the ``[project.dependencies]`` spec is a
    bare name and the pinned tag lives here instead.
    """
    tool = data.get("tool", {})
    if not isinstance(tool, Mapping):
        return None
    uv = tool.get("uv", {})
    if not isinstance(uv, Mapping):
        return None
    sources = uv.get("sources", {})
    if not isinstance(sources, Mapping):
        return None
    for name, source in sources.items():
        if _normalize(str(name)) == _normalize(package) and isinstance(source, Mapping):
            tag = source.get("tag")
            if isinstance(tag, str):
                return tag
    return None


def resolve_declared_version(pyproject_path: Path, package: str) -> str | None:
    """The version the consumer DECLARES for ``package`` in its manifest, or ``None``.

    Reads the dependency specs first (a direct-URL ``@tag`` or a ``==`` pin), then
    a ``[tool.uv.sources]`` tag. A missing / unreadable / malformed manifest, or
    a manifest that does not mention ``package``, yields ``None``.
    """
    try:
        text = pyproject_path.read_text(encoding="utf-8")
    except OSError:
        return None
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError:
        return None
    for spec in _iter_dependency_specs(data):
        pin = _pin_from_spec(spec, package)
        if pin is not None:
            return pin
    return _uv_source_tag(data, package)


class EngineVersionFloor(FitnessRule):
    """Flags a consumer whose pinned engine version is below the floor."""

    name = "engine-version-floor"
    remediation = REMEDIATION

    #: Config (repo-neutral defaults; overridden per consumer via from_config).
    floor: str = ""
    package: str = DEFAULT_PACKAGE

    @classmethod
    def from_config(
        cls,
        config: Mapping[str, Any],
        *,
        repo_root: Path | None = None,
    ) -> EngineVersionFloor:
        rule = super().from_config(config, repo_root=repo_root)
        assert isinstance(rule, EngineVersionFloor)  # noqa: S101  # narrowing for mypy
        floor = config.get("floor")
        rule.floor = str(floor) if floor else ""
        rule.package = str(config.get("package", DEFAULT_PACKAGE))
        return rule

    def _installed_version(self) -> str | None:
        """The installed distribution's own version, or ``None`` when absent."""
        try:
            return _metadata.version(self.package)
        except _metadata.PackageNotFoundError:
            return None

    def resolve_version(self) -> str | None:
        """The consumer's DECLARED pin, else the installed distribution version."""
        declared = resolve_declared_version(self._repo_root / _PYPROJECT, self.package)
        return declared if declared is not None else self._installed_version()

    def file_has_violation(self, path: Path) -> bool:
        """Unused — the unit of violation is the version pin, not a file."""
        return False

    def enumerate_files(self) -> list[Path]:
        """No file surface — the pin lives in the manifest, not the tree."""
        return []

    def collect_violations(self) -> set[Path]:
        """A single violation entry iff the resolved pin is below the floor.

        No floor configured, an unparseable floor, or an unresolvable pin are all
        guard-forward no-ops (empty set): a consumer is never hard-broken before
        it can determine and bump its own version.
        """
        if not self.floor:
            return set()
        floor = parse_version(self.floor)
        if floor is None:
            return set()
        resolved = self.resolve_version()
        if resolved is None:
            return set()
        pinned = parse_version(resolved)
        if pinned is None or pinned >= floor:
            return set()
        return {Path(f"{self.package} pinned {resolved} is below floor {self.floor}")}


def build(
    config: Mapping[str, Any],
    *,
    repo_root: Path | None = None,
) -> EngineVersionFloor:
    """Factory the engine calls to bind this CORE check to a consumer's config."""
    return EngineVersionFloor.from_config(config, repo_root=repo_root)


def main(argv: list[str] | None = None) -> int:
    """CLI entry — supports ``--establish-baseline`` and ``--repo-root``."""
    return run_core_check(EngineVersionFloor, argv)


if __name__ == "__main__":
    import sys

    sys.exit(main())
