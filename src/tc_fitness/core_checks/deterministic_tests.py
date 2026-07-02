"""CORE check: deterministic_tests — test non-determinism is a GATE FAILURE.

A ~50% flake sat undetected in a consumer's coverage suite: the same commit
passed on some runs and failed on others, and the gate tolerated it because a
green-on-retry looked like a pass. This check converges that one-off lesson
INTO THE ENGINE so *every* repo inherits the same bite: a test whose outcome is
not stable is a failure the gate REFUSES to pass, surfaced with the offending
test id, not silently retried into green.

What it proves
==============
Given the (changed-scope) test roots a consumer configures, this check runs the
suite several times and FAILS if any test's outcome is not identical across all
runs. Two independent probes, both under one pinned hash seed
(``PYTHONHASHSEED``) so hash-ordering is held constant:

* **fixed-seed repeat** — run the suite ``repeats`` times in the SAME order.
  Any divergence here is pure non-determinism (wall-clock, unseeded ``random``,
  network, shared/leaked state, filesystem-order dependence) — the exact class
  the undetected coverage flake belonged to.

* **order stability** — re-run the SAME tests under one or more shuffled
  execution orders and require the SAME per-test verdicts. An outcome that
  flips when the order changes is an order-dependent test (state leaked between
  tests). Order is varied deterministically from an integer ``order_seed`` so a
  failure REPRODUCES exactly — non-determinism surfaced AS a deterministic
  failure. When a consumer has adopted ``pytest-randomly`` the same seed is
  handed to ``--randomly-seed`` (``use_randomly = true``); otherwise the check
  reorders the collected node ids itself, so it bites with core pytest alone.

Reconciliation with the loop determinism guardrail
==================================================
The loop state machine bans ``--reruns`` and the companion CORE check
``no_test_reruns`` fails any repo whose pytest/CI config enables flaky-retry
(``pytest-rerunfailures`` / ``--reruns`` / retry actions) — because a retry
MASKS the very flake this gate exists to catch. This check is the dynamic half
of the same standard and never contradicts it:

* it NEVER passes ``--reruns`` / enables a retry plugin, and
* it defensively blocks the retry plugin inside its own probe
  (``-p no:rerunfailures``) so a consumer's leftover ``addopts = "--reruns=N"``
  cannot mask a flake DURING the determinism run.

``no_test_reruns`` enforces the ban statically (the config may not ask for
retries); ``deterministic_tests`` proves the payoff dynamically (the suite is
actually stable). Spec (STANDARDS.md), gate (these two checks), and runtime
(the loop guardrail) therefore agree. See ``docs/STANDARDS.md`` §Deterministic
tests.

Repo-agnostic
=============
This module names no repo. The test roots, the pinned seed, the repeat count,
the order seeds, the per-run timeout, the base test command, and whether to
delegate ordering to ``pytest-randomly`` all arrive through the consumer's
``[tool.tc_fitness.core_checks.deterministic_tests]`` block. With NO config the
default ``roots = ()`` enumerates nothing and the check is a vacuous pass — the
same adoption contract every CORE check follows.
"""

from __future__ import annotations

import os
import random
import re
import subprocess
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from tc_fitness.core_checks import run_core_check
from tc_fitness.fitness_rule import FitnessRule
from tc_fitness.lib import remediation as _remediation

#: The pinned hash seed. Held CONSTANT across every run so hash-ordering is not
#: itself a variable — a divergence under a fixed seed is a real flake.
DEFAULT_SEED = 0
#: How many times the suite is run in the natural order. Two is enough to catch
#: a coin-flip flake most of the time; a consumer may raise it.
DEFAULT_REPEATS = 2
#: The order seeds probed for order-dependence. Each produces one extra run in a
#: distinct, reproducible order. Empty disables the order probe.
DEFAULT_ORDER_SEEDS: tuple[int, ...] = (1, 2)
#: The base command the runs are built on. ``python -m pytest`` by default; a
#: consumer on ``uv`` / a wrapper overrides it.
DEFAULT_TEST_COMMAND: tuple[str, ...] = ("python", "-m", "pytest")
#: Per-run wall-clock ceiling. A run that does not finish in this window is a
#: FAIL (the suite cannot be shown deterministic).
DEFAULT_TIMEOUT_SECONDS = 900

#: Sentinel outcome for a test that did not appear in a run at all (collection
#: non-determinism is itself instability).
_ABSENT = "<absent>"

# A verbose pytest progress line: ``<nodeid> <OUTCOME>  [ NN%]``. The trailing
# ``[ NN%]`` marker is present ONLY on the per-test progress lines, never on the
# terminal summary (``FAILED <nodeid> - <reason>``), so requiring it cleanly
# excludes the summary from the parse.
_OUTCOME_LINE = re.compile(
    r"^(?P<nodeid>.+?::.+?)\s+(?P<outcome>PASSED|FAILED|ERROR|SKIPPED|XFAIL|XPASS)\s+\[\s*\d+%\]\s*$"
)

REMEDIATION = _remediation(
    fix=(
        "a test whose verdict is not stable is a DEFECT, not noise: find the "
        "shared/leaked state, the unseeded random, the wall-clock or ordering "
        "dependence and fix the ROOT CAUSE so the test is stable in any order. "
        "Do NOT paper over it with --reruns / a retry plugin (that masks the "
        "flake and is banned by no_test_reruns + the loop guardrail). If it "
        "cannot be fixed now, open a must-fix Linear work-item and quarantine "
        "the test explicitly there — never leave it silently retried."
    ),
    nxt="re-run this check to confirm the suite is stable across seeds and orders.",
    run="python -m tc_fitness.core_checks.deterministic_tests",
    passing="isolate per-test state (fresh fixtures, seeded randomness) so order never changes a verdict",
    forbidden="enable pytest-rerunfailures / --reruns to retry a flaky test into green",
)


@dataclass(frozen=True)
class RunSpec:
    """One planned execution of the suite.

    ``order_seed`` is ``None`` for a natural-order run (the fixed-seed repeats)
    and an integer for an order-probe run. ``label`` is the human tag shown
    against a divergence so the reader sees WHICH probe caught it.
    """

    label: str
    order_seed: int | None


@dataclass(frozen=True)
class Divergence:
    """A test whose outcome was not identical across the planned runs."""

    test_id: str
    #: ``(label, outcome)`` for every run, in plan order.
    outcomes: tuple[tuple[str, str], ...]


class SuiteRunError(RuntimeError):
    """A run could not be completed (timeout / collection failure)."""


# --------------------------------------------------------------------------- #
# Pure helpers — the detection core, unit-tested without a subprocess.
# --------------------------------------------------------------------------- #


def parse_outcomes(stdout: str) -> dict[str, str]:
    """Map ``nodeid -> lowercased outcome`` from ``pytest -v --color=no`` output.

    Only the verbose per-test progress lines are read; the terminal summary and
    any captured output are ignored (they lack the ``[ NN%]`` progress marker).
    """
    out: dict[str, str] = {}
    for line in stdout.splitlines():
        match = _OUTCOME_LINE.match(line.rstrip())
        if match:
            out[match.group("nodeid").strip()] = match.group("outcome").lower()
    return out


def parse_collected(stdout: str) -> list[str]:
    """Return the node ids from ``pytest --collect-only -q`` output, in order.

    A collect line is any line naming a node id (contains ``::``); the trailing
    ``N tests collected`` summary carries no ``::`` and is dropped.
    """
    return [line.strip() for line in stdout.splitlines() if "::" in line and line.strip()]


def shuffled_order(node_ids: Sequence[str], seed: int) -> list[str]:
    """A reproducible reordering of ``node_ids`` under ``seed``.

    Deterministic in ``seed`` (its own ``random.Random``), so an order-dependent
    failure reproduces exactly rather than intermittently.
    """
    items = list(node_ids)
    # A REPRODUCIBLE shuffle is the whole point (a failing order must replay
    # exactly); this is not a security context, so the stdlib PRNG is correct.
    random.Random(seed).shuffle(items)  # noqa: S311  # reproducibility, not cryptography
    return items


def plan_runs(repeats: int, order_seeds: Sequence[int]) -> list[RunSpec]:
    """Build the run plan: ``repeats`` natural-order runs + one per order seed."""
    plan = [RunSpec(label=f"fixed-seed:rep{i + 1}", order_seed=None) for i in range(max(repeats, 1))]
    plan += [RunSpec(label=f"order:seed{seed}", order_seed=seed) for seed in order_seeds]
    return plan


def compare_runs(runs: Sequence[tuple[str, Mapping[str, str]]]) -> list[Divergence]:
    """Return the tests whose outcome was not identical across ``runs``.

    ``runs`` is ``(label, {nodeid: outcome})`` in plan order. A test is a
    divergence when the set of outcomes it shows across the runs (with
    :data:`_ABSENT` standing in where a run never ran it) has more than one
    distinct value. The result is sorted by test id for a stable emit.
    """
    all_ids: set[str] = set()
    for _label, outcomes in runs:
        all_ids.update(outcomes)

    divergences: list[Divergence] = []
    for test_id in sorted(all_ids):
        per_run = tuple((label, outcomes.get(test_id, _ABSENT)) for label, outcomes in runs)
        if len({outcome for _label, outcome in per_run}) > 1:
            divergences.append(Divergence(test_id=test_id, outcomes=per_run))
    return divergences


def build_pytest_argv(
    command: Sequence[str],
    *,
    test_paths: Sequence[str],
    node_ids: Sequence[str] | None,
    order_seed: int | None,
    use_randomly: bool,
) -> list[str]:
    """Assemble the pytest argv for one run.

    Invariants that make the probe trustworthy: verbose per-test reporting with
    no colour (so :func:`parse_outcomes` sees clean lines), the cache provider
    off (no run-to-run cache state), and the rerun plugin explicitly BLOCKED
    (``-p no:rerunfailures``) so a stray ``--reruns`` in the consumer's config
    can never mask a flake mid-probe. When ``use_randomly`` is off, the built-in
    ``pytest-randomly`` shuffle is disabled too so THIS check's ordering is the
    only variable.
    """
    argv = [
        *command,
        "-v",
        "--color=no",
        "-p",
        "no:cacheprovider",
        "-p",
        "no:rerunfailures",
    ]
    if use_randomly:
        # Delegate ordering to pytest-randomly: a natural run pins the base
        # seed (so repeats are identical); an order run uses the probe seed.
        argv += ["--randomly-seed", str(order_seed if order_seed is not None else DEFAULT_SEED)]
        argv += list(test_paths)
    else:
        # Own the ordering: block the randomly plugin, run the natural order by
        # path, or the explicit shuffled node ids for an order probe.
        argv += ["-p", "no:randomly"]
        if order_seed is None or not node_ids:
            argv += list(test_paths)
        else:
            argv += shuffled_order(node_ids, order_seed)
    return argv


def _run_env(seed: int) -> dict[str, str]:
    """The child environment: the current env with ``PYTHONHASHSEED`` pinned."""
    env = dict(os.environ)
    env["PYTHONHASHSEED"] = str(seed)
    return env


# --------------------------------------------------------------------------- #
# The real subprocess runner (the dependency-injection seam).
# --------------------------------------------------------------------------- #


def collect_node_ids(
    command: Sequence[str],
    test_paths: Sequence[str],
    *,
    repo_root: Path,
    seed: int,
    timeout: int,
) -> list[str]:
    """Collect the node ids under ``test_paths`` (for the order probe)."""
    argv = [*command, "--collect-only", "-q", "-p", "no:cacheprovider", "-p", "no:randomly", *test_paths]
    try:
        result = subprocess.run(  # noqa: S603  # argv is the configured test command + repo paths
            argv,
            cwd=repo_root,
            env=_run_env(seed),
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:  # pragma: no cover - defensive
        raise SuiteRunError(f"collection timed out after {timeout}s") from exc
    return parse_collected(result.stdout)


def run_suite(
    spec: RunSpec,
    *,
    command: Sequence[str],
    test_paths: Sequence[str],
    node_ids: Sequence[str],
    repo_root: Path,
    seed: int,
    use_randomly: bool,
    timeout: int,
) -> dict[str, str]:
    """Execute one planned run and return its ``{nodeid: outcome}`` map."""
    argv = build_pytest_argv(
        command,
        test_paths=test_paths,
        node_ids=node_ids,
        order_seed=spec.order_seed,
        use_randomly=use_randomly,
    )
    try:
        result = subprocess.run(  # noqa: S603  # argv is the configured test command + repo node ids
            argv,
            cwd=repo_root,
            env=_run_env(seed),
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise SuiteRunError(f"run {spec.label!r} timed out after {timeout}s") from exc
    outcomes = parse_outcomes(result.stdout)
    if not outcomes:
        raise SuiteRunError(
            f"run {spec.label!r} produced no per-test results (exit {result.returncode}); "
            "the suite did not run — check the configured test command and roots."
        )
    return outcomes


#: The runner signature the orchestrator depends on (injectable for tests).
Runner = Callable[[RunSpec], Mapping[str, str]]


def detect_nondeterminism(
    plan: Sequence[RunSpec],
    runner: Runner,
) -> list[Divergence]:
    """Execute every planned run via ``runner`` and compare the outcomes.

    ``runner`` maps a :class:`RunSpec` to that run's ``{nodeid: outcome}``. The
    real path binds it to :func:`run_suite`; a test injects a fake to drive the
    comparison without a subprocess.
    """
    runs = [(spec.label, dict(runner(spec))) for spec in plan]
    return compare_runs(runs)


def format_failure(divergences: Sequence[Divergence]) -> str:
    """Render the FAIL block: each unstable test and its per-run verdicts."""
    lines = [
        f"FAIL [deterministic-tests] — {len(divergences)} test(s) with a non-deterministic outcome:",
    ]
    for div in divergences:
        detail = ", ".join(f"{label}={outcome}" for label, outcome in div.outcomes)
        lines.append(f"  {div.test_id}: {detail}")
    lines.append("")
    lines.append(REMEDIATION)
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# The FitnessRule binding.
# --------------------------------------------------------------------------- #


class DeterministicTests(FitnessRule):
    """Gate that FAILS when a configured test suite is not run-to-run stable.

    Unlike a file-scan rule this has no per-file baseline — non-determinism is
    not a grandfatherable debt, it is a hard gate — so :meth:`run` is overridden
    to drive the suite rather than compare a violation set against a baseline.
    """

    name = "deterministic-tests"
    remediation = REMEDIATION

    #: Rule-specific knobs (overridden from the consumer's config block).
    seed: int = DEFAULT_SEED
    repeats: int = DEFAULT_REPEATS
    order_seeds: tuple[int, ...] = DEFAULT_ORDER_SEEDS
    test_command: tuple[str, ...] = DEFAULT_TEST_COMMAND
    use_randomly: bool = False
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS

    @classmethod
    def from_config(
        cls,
        config: Mapping[str, Any],
        *,
        repo_root: Path | None = None,
    ) -> DeterministicTests:
        rule = super().from_config(config, repo_root=repo_root)
        assert isinstance(rule, DeterministicTests)  # noqa: S101  # narrowing for mypy
        rule.seed = int(config.get("seed", DEFAULT_SEED))
        rule.repeats = int(config.get("repeats", DEFAULT_REPEATS))
        order_seeds = config.get("order_seeds")
        rule.order_seeds = (
            tuple(int(s) for s in order_seeds) if order_seeds is not None else DEFAULT_ORDER_SEEDS
        )
        command = config.get("test_command")
        rule.test_command = tuple(str(c) for c in command) if command else DEFAULT_TEST_COMMAND
        rule.use_randomly = bool(config.get("use_randomly", False))
        rule.timeout_seconds = int(config.get("timeout_seconds", DEFAULT_TIMEOUT_SECONDS))
        return rule

    def file_has_violation(self, path: Path) -> bool:  # pragma: no cover - not used
        """Unused: determinism is a behavioural gate, not a per-file scan."""
        return False

    def _test_paths(self) -> list[str]:
        """The configured, on-disk test roots (repo-relative), or ``[]``."""
        return [root for root in self._roots if (self._repo_root / root).exists()]

    def _runner(self, test_paths: Sequence[str], node_ids: Sequence[str]) -> Runner:
        """Bind :func:`run_suite` to this rule's config for the orchestrator."""

        def _run(spec: RunSpec) -> Mapping[str, str]:
            return run_suite(
                spec,
                command=self.test_command,
                test_paths=test_paths,
                node_ids=node_ids,
                repo_root=self._repo_root,
                seed=self.seed,
                use_randomly=self.use_randomly,
                timeout=self.timeout_seconds,
            )

        return _run

    def run(self) -> int:
        """Run the plan; FAIL (1) on any non-determinism, else PASS (0).

        With no configured roots (the adoption default) there is nothing to run
        and the gate passes vacuously — identical to every other CORE check.
        """
        test_paths = self._test_paths()
        if not test_paths:
            print("ok [deterministic-tests] — no test roots configured; nothing to check.")
            return 0

        plan = plan_runs(self.repeats, self.order_seeds)
        need_node_ids = not self.use_randomly and any(spec.order_seed is not None for spec in plan)
        node_ids: list[str] = []
        if need_node_ids:
            node_ids = collect_node_ids(
                self.test_command,
                test_paths,
                repo_root=self._repo_root,
                seed=self.seed,
                timeout=self.timeout_seconds,
            )
            if not node_ids:
                print("ok [deterministic-tests] — no tests collected under the configured roots.")
                return 0

        try:
            divergences = detect_nondeterminism(plan, self._runner(test_paths, node_ids))
        except SuiteRunError as exc:
            print(f"FAIL [deterministic-tests] — could not establish determinism: {exc}")
            print()
            print(self.remediation)
            return 1

        if divergences:
            print(format_failure(divergences))
            return 1
        print(f"ok [deterministic-tests] — stable across {len(plan)} runs (seed={self.seed}).")
        return 0


def build(config: Mapping[str, Any], *, repo_root: Path | None = None) -> DeterministicTests:
    """Factory the engine calls to bind this CORE check to a consumer's config."""
    return DeterministicTests.from_config(config, repo_root=repo_root)


def main(argv: list[str] | None = None) -> int:
    """CLI entry — supports ``--establish-baseline`` and ``--repo-root``."""
    return run_core_check(DeterministicTests, argv)


if __name__ == "__main__":
    import sys

    sys.exit(main())
