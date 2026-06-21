"""CORE check: schema_conformance — every data file carries its required keys.

A family of data files (brand-token sheets, manifests, config fragments) shares
a contract: each MUST provide a set of required top-level keys. A file missing
one renders an unstyled component, a half-configured connector, or a crash at
load time. This rule proves the required key set is present in every file in
scope BEFORE anything consumes it.

The required key set is CONFIG (``required_keys``) the consumer supplies — the
repo-agnostic generalisation of a schema. The donor check derived the key set
from a TypeScript interface, which is repo-coupled; here the consumer declares
the keys (or points a follow-on check at a schema). Files are parsed as YAML
(a superset of JSON, so ``.json`` parses too); a file that is not a mapping, or
that omits a required key, is a violation.

Ported from tc-agent-zone ``scripts/checks/brand_tokens_schema_validate.py``
(ADR-022 D5) — re-expressed as a configurable, repo-agnostic rule. The scan
roots, the file extensions, and the required key set are all CONFIG; nothing
here names a repo, a directory, or a field.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from tc_fitness.core_checks import run_core_check
from tc_fitness.fitness_rule import FitnessRule
from tc_fitness.lib import remediation as _remediation

#: A schema with no declared required keys imposes no constraint — every file
#: is conformant. The consumer narrows this via ``required_keys``.
DEFAULT_REQUIRED_KEYS: tuple[str, ...] = ()

REMEDIATION = _remediation(
    fix=(
        "add the missing required key(s) to the data file so it carries the "
        "full contract every consumer of this file family depends on. The "
        "required key set is declared in this check's config block."
    ),
    nxt="re-run this check to confirm the file is conformant.",
    run="python -m tc_fitness.core_checks.schema_conformance",
    passing="every required key present at the top level of the mapping",
    forbidden="ship a data file missing a key its consumers read at load time",
)


def _load_mapping(path: Path) -> dict[str, Any] | None:
    """Parse ``path`` as YAML/JSON; return the mapping, or None if not a mapping.

    A decode/parse error or a non-mapping document returns ``None`` — the rule
    treats a file it cannot read as a mapping as a violation (it cannot prove
    conformance). PyYAML is imported lazily so a consumer that never binds this
    check need not install it.
    """
    try:
        import yaml
    except ImportError:  # pragma: no cover - exercised only on hosts without PyYAML
        return None
    try:
        loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (yaml.YAMLError, UnicodeDecodeError):
        return None
    return loaded if isinstance(loaded, dict) else None


def file_missing_required_keys(path: Path, *, required_keys: tuple[str, ...]) -> bool:
    """True iff ``path`` is not a mapping OR omits any of ``required_keys``.

    Pure helper (the detection core) so tests assert on it directly. With an
    empty ``required_keys`` every readable mapping is conformant; an unreadable
    or non-mapping file is always a violation (conformance is unprovable).
    """
    mapping = _load_mapping(path)
    if mapping is None:
        return True
    return any(key not in mapping for key in required_keys)


class SchemaConformance(FitnessRule):
    """Flags data files that omit a required schema key (ADR-022 D5 shape)."""

    name = "schema-conformance"
    remediation = REMEDIATION
    extensions = (".yaml", ".yml", ".json")

    #: Rule-specific knob — instance attr so ``from_config`` overrides it.
    required_keys: tuple[str, ...] = DEFAULT_REQUIRED_KEYS

    @classmethod
    def from_config(
        cls,
        config: Mapping[str, Any],
        *,
        repo_root: Path | None = None,
    ) -> SchemaConformance:
        """Build from config, also reading the ``required_keys`` schema."""
        rule = super().from_config(config, repo_root=repo_root)
        assert isinstance(rule, SchemaConformance)  # noqa: S101  # narrowing for mypy
        keys = config.get("required_keys", DEFAULT_REQUIRED_KEYS)
        rule.required_keys = tuple(keys)
        return rule

    def file_has_violation(self, path: Path) -> bool:
        return file_missing_required_keys(path, required_keys=self.required_keys)


def build(
    config: Mapping[str, Any],
    *,
    repo_root: Path | None = None,
) -> SchemaConformance:
    """Factory the engine calls to bind this CORE check to a consumer's config."""
    return SchemaConformance.from_config(config, repo_root=repo_root)


def main(argv: list[str] | None = None) -> int:
    """CLI entry — supports ``--establish-baseline`` and ``--repo-root``."""
    return run_core_check(SchemaConformance, argv)


if __name__ == "__main__":
    import sys

    sys.exit(main())
