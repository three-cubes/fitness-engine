"""CORE check: ci_fanin_parity — every CI job gates the merge or says it doesn't.

Branch protection typically requires exactly ONE status context from the CI
workflow: the terminal aggregator job whose ``needs:`` fan-in aggregates every
blocking stage. A green merge is only as safe as that fan-in is COMPLETE. A job
defined in the workflow but NOT reachable from the aggregator's transitive
``needs:`` closure does not block the merge — it can run, fail, and the PR ships
green. This rule proves the workflow is INTERNALLY HONEST: every non-gating job
explicitly SAYS it is non-gating via an informational marker comment.

The check parses the configured workflow, finds the aggregator job (by its
``name:``), builds the transitive ``needs:`` closure, and flags the workflow when
any job is NEITHER in that closure, NOR the aggregator itself, NOR carrying an
``# fan-in: informational`` marker comment in the lines directly above its key.

Ported from kairix ``scripts/checks/check_f93_ci_fanin_parity.py`` (EPIC #499
Phase 2) and re-expressed as a configurable, repo-agnostic rule. The donor
hardcoded ``.github/workflows/ci.yml`` and the ``CI gate`` aggregator name; here
both are config:

* ``workflow`` — repo-relative path to the CI workflow file.
* ``aggregator_name`` — the ``name:`` of the job producing the required context.
* ``informational_marker`` — the comment prefix that marks a job non-gating.

Modelling note: the FitnessRule baseline is per-FILE, so a dishonest fan-in
surfaces as one violation (the workflow path). The remediation names the class;
the operator reads the workflow to find the specific dangling job(s).
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from tc_fitness.core_checks import run_core_check
from tc_fitness.fitness_rule import FitnessRule
from tc_fitness.lib import remediation as _remediation

#: The job ``name:`` whose status context branch protection requires. Overridable.
DEFAULT_AGGREGATOR_NAME = "CI gate"

#: The comment prefix declaring a job legitimately outside the gate fan-in.
DEFAULT_INFORMATIONAL_MARKER = "# fan-in: informational"

#: The CI workflow this rule governs. Overridable via config.
DEFAULT_WORKFLOW = ".github/workflows/ci.yml"

REMEDIATION = _remediation(
    fix=(
        "decide whether the dangling job SHOULD gate the merge. If yes, add its "
        "id to the aggregator job's needs: list (and its result-evaluation "
        "loop). If no, add a marker comment on the lines directly above the "
        "job's key: '# fan-in: informational - <why it is non-gating>'."
    ),
    nxt="re-run this check to confirm the fan-in is honest.",
    run="python -m tc_fitness.core_checks.ci_fanin_parity",
    passing="check:\\n    name: CI gate\\n    needs: [unit, security, docker]",
    forbidden="license-scan defined but absent from the CI-gate needs: and unmarked",
)


def _load_jobs(workflow_text: str) -> dict[str, Any]:
    """Parse the workflow and return its ``jobs`` mapping ({} on any failure).

    PyYAML is imported lazily (it is an optional extra): when absent the rule
    degrades to "nothing to assert" rather than crashing the gate.
    """
    try:
        import yaml
    except ImportError:
        return {}
    try:
        data = yaml.safe_load(workflow_text)
    except yaml.YAMLError:
        return {}
    if not isinstance(data, dict):
        return {}
    jobs = data.get("jobs")
    return jobs if isinstance(jobs, dict) else {}


def _needs_of(job_spec: object) -> list[str]:
    """Normalise a job's ``needs:`` (scalar or sequence) to a list of strings."""
    if not isinstance(job_spec, dict):
        return []
    needs = job_spec.get("needs")
    if isinstance(needs, str):
        return [needs]
    if isinstance(needs, list):
        return [n for n in needs if isinstance(n, str)]
    return []


def _find_aggregator(jobs: dict[str, Any], aggregator_name: str) -> str | None:
    """Return the job id whose ``name:`` is ``aggregator_name``, else None."""
    for job_id, spec in jobs.items():
        if isinstance(spec, dict) and spec.get("name") == aggregator_name:
            return job_id
    return None


def _closure(jobs: dict[str, Any], root: str) -> set[str]:
    """Transitive ``needs:`` closure rooted at ``root`` (root excluded)."""
    seen: set[str] = set()
    frontier = list(_needs_of(jobs.get(root)))
    while frontier:
        node = frontier.pop()
        if node in seen or node not in jobs:
            continue
        seen.add(node)
        frontier.extend(_needs_of(jobs.get(node)))
    return seen


def _jobs_marked_informational(workflow_text: str, job_ids: set[str], marker: str) -> set[str]:
    """Job ids carrying the informational ``marker`` in the comment block above.

    PyYAML discards comments, so this scans the raw text. A job key sits at
    exactly two-space indent under ``jobs:`` and ends in a bare colon.
    """
    lines = workflow_text.splitlines()
    marked: set[str] = set()
    for idx, raw in enumerate(lines):
        stripped = raw.strip()
        if not (raw.startswith("  ") and raw[2:3] != " "):
            continue
        if not stripped.endswith(":"):
            continue
        job_id = stripped[:-1].strip()
        if job_id not in job_ids:
            continue
        cursor = idx - 1
        while cursor >= 0:
            above = lines[cursor].strip()
            if above.startswith("#"):
                if marker in lines[cursor]:
                    marked.add(job_id)
                    break
                cursor -= 1
                continue
            break
    return marked


def workflow_fanin_is_dishonest(
    path: Path,
    *,
    aggregator_name: str,
    informational_marker: str,
) -> bool:
    """True iff the workflow at ``path`` has a dangling (un-gated, un-marked) job.

    Pure helper (the detection core) so tests can assert on it directly. Returns
    True when the aggregator is missing entirely, or when any non-aggregator job
    is neither in the aggregator's needs-closure nor marked informational. An
    unreadable / job-less workflow is treated as "nothing to assert" (False).
    """
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return False
    jobs = _load_jobs(text)
    if not jobs:
        return False
    aggregator = _find_aggregator(jobs, aggregator_name)
    if aggregator is None:
        return True
    gated = _closure(jobs, aggregator)
    informational = _jobs_marked_informational(text, set(jobs), informational_marker)
    for job_id in jobs:
        if job_id == aggregator or job_id in gated or job_id in informational:
            continue
        return True
    return False


class CiFaninParity(FitnessRule):
    """Flags a CI workflow whose gate fan-in is dishonest."""

    name = "ci-fanin-parity"
    remediation = REMEDIATION
    extensions = (".yml", ".yaml")

    workflow: str = DEFAULT_WORKFLOW
    aggregator_name: str = DEFAULT_AGGREGATOR_NAME
    informational_marker: str = DEFAULT_INFORMATIONAL_MARKER

    @classmethod
    def from_config(
        cls,
        config: Mapping[str, Any],
        *,
        repo_root: Path | None = None,
    ) -> CiFaninParity:
        rule = super().from_config(config, repo_root=repo_root)
        assert isinstance(rule, CiFaninParity)  # noqa: S101  # narrowing for mypy
        workflow = config.get("workflow")
        if workflow is not None:
            rule.workflow = str(workflow)
        aggregator = config.get("aggregator_name")
        if aggregator is not None:
            rule.aggregator_name = str(aggregator)
        marker = config.get("informational_marker")
        if marker is not None:
            rule.informational_marker = str(marker)
        return rule

    def is_in_scope(self, rel: str) -> bool:
        # Scope is the single configured workflow file.
        return True

    def enumerate_files(self) -> list[Path]:
        """Enumerate just the configured workflow file (when it exists)."""
        workflow_path = self._repo_root / self.workflow
        return [workflow_path] if workflow_path.is_file() else []

    def file_has_violation(self, path: Path) -> bool:
        return workflow_fanin_is_dishonest(
            path,
            aggregator_name=self.aggregator_name,
            informational_marker=self.informational_marker,
        )


def build(
    config: Mapping[str, Any],
    *,
    repo_root: Path | None = None,
) -> CiFaninParity:
    """Factory the engine calls to bind this CORE check to a consumer's config."""
    return CiFaninParity.from_config(config, repo_root=repo_root)


def main(argv: list[str] | None = None) -> int:
    """CLI entry — supports ``--establish-baseline`` and ``--repo-root``."""
    return run_core_check(CiFaninParity, argv)


if __name__ == "__main__":
    import sys

    sys.exit(main())
