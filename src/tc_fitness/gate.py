"""``tc-fitness run`` — the single runnable gate both CI and local invoke.

This is the load-bearing surface of "local == CI by construction". One binary,
reading one repo-local config (:mod:`tc_fitness.gate_config`), runs the repo's
WHOLE quality gate in order:

* CI's reusable ``python-quality-gate.yml`` shrinks to
  ``checkout → setup-uv → uv run tc-fitness run``.
* A repo's ``make check`` becomes ``uv run tc-fitness run``.

Because both shell out to the SAME command reading the SAME ``[tool.tc_fitness]``
declaration, the gate has exactly one definition. There is no hand-copied pytest
block to drift between ``scripts/ci/check.sh`` and ``ci.yml``.

What it does NOT do
-------------------
The engine orchestrates STEPS; it never owns a repo's specifics. The pytest
scope, the ``--cov`` roots, the ruff/bandit targets, the detect-secrets baseline,
and the consumer's fitness-check catalogue are all CONFIG (each a declared step),
never baked into this module. Adding a step is a config edit in the consumer,
not an engine change — that is the whole point (a reusable workflow that took
these as *inputs* would just relocate the per-repo coupling into YAML).

Step kinds
----------
* ``run`` — a command vector (argv), executed without a shell.
* ``shell`` — a shell command string (for pipelines / globs / ``$(...)``).
* ``catalogue`` — the consumer's ``module:attr`` RuleEntry catalogue, dispatched
  IN-PROCESS through :func:`tc_fitness.runner.main_cli` (no second python boot;
  the runner's own named ledger prints inline).

Output contract (gate-runner discipline)
----------------------------------------
A ``run [id]`` line and a ``PASS`` / ``FAIL`` / ``SKIP`` verdict per step, the
step's own stdout/stderr inline, then a final aggregate verdict. A FAILing step
prints its agent-actionable ``fix:`` / ``next:`` lines. The process exit is
non-zero iff any gating step failed (a ``continue_on_error`` step never gates).
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from importlib import import_module
from pathlib import Path

from tc_fitness.gate_config import (
    GateConfig,
    GateConfigError,
    StepSpec,
    load_config,
)
from tc_fitness.runner import Colours, main_cli

_RED = Colours.RED
_GREEN = Colours.GREEN
_YELLOW = Colours.YELLOW
_RESET = Colours.RESET


@dataclass
class StepResult:
    """The outcome of one step: pass / fail / skip, plus whether it gates."""

    id: str
    status: str  # "pass" | "fail" | "skip"
    gating: bool = True  # a continue_on_error fail is non-gating

    @property
    def is_gating_failure(self) -> bool:
        return self.status == "fail" and self.gating


@dataclass
class GateOutcome:
    """The aggregate result of a ``tc-fitness run``."""

    results: list[StepResult] = field(default_factory=list)

    @property
    def ran(self) -> int:
        return sum(1 for r in self.results if r.status != "skip")

    @property
    def skipped(self) -> int:
        return sum(1 for r in self.results if r.status == "skip")

    @property
    def gating_failures(self) -> list[str]:
        return [r.id for r in self.results if r.is_gating_failure]

    @property
    def ok(self) -> bool:
        return not self.gating_failures

    @property
    def exit_code(self) -> int:
        return 0 if self.ok else 1


def _step_env(step: StepSpec) -> dict[str, str]:
    """The child environment: the inherited env overlaid with the step's env."""
    env = dict(os.environ)
    env.update(step.env)
    return env


def _program_on_path(argv0: str, env: dict[str, str]) -> bool:
    """Whether the step's program resolves on PATH (honouring the step env)."""
    return shutil.which(argv0, path=env.get("PATH")) is not None


def _print_fix_next(step: StepSpec) -> None:
    """Print the step's agent-actionable remediation under its FAIL, if declared."""
    if step.fix:
        print(f"   fix: {step.fix}")
    if step.next:
        print(f"   next: {step.next}")


def _run_command_step(step: StepSpec, repo_root: Path) -> StepResult:
    """Execute a ``run`` (argv) or ``shell`` (string) step as a child process.

    Inherits stdout/stderr so the child's output streams live (no capture) — the
    gate is a foreground command, not a log scraper. A missing program is a SKIP
    when ``allow_missing`` else a FAIL with an actionable hint.
    """
    env = _step_env(step)
    cwd = (repo_root / step.cwd).resolve()
    label = step.summary or step.id

    print(f"{_YELLOW}run [{step.id}]{_RESET} {label}")

    if step.kind == "run":
        # `kind == "run"` guarantees `run` is set (the loader enforces exactly
        # one action per step), so the `or ()` is unreachable — present only to
        # satisfy the type checker without an `assert` in shipped code.
        argv = list(step.run or ())
        if not _program_on_path(argv[0], env):
            if step.allow_missing:
                print(f"{_YELLOW}SKIP [{step.id}]{_RESET} {argv[0]} not on PATH (allow_missing)")
                return StepResult(step.id, "skip")
            print(f"{_RED}FAIL [{step.id}]{_RESET} {argv[0]} not on PATH")
            print(f"   fix: install {argv[0]} (or set allow_missing = true for this step)")
            _print_fix_next(step)
            return StepResult(step.id, "fail", gating=not step.continue_on_error)
        proc = subprocess.run(argv, cwd=cwd, env=env, check=False)  # noqa: S603 - argv is config, not user input
    else:  # shell
        # `kind == "shell"` guarantees `shell` is set (loader invariant).
        proc = subprocess.run(  # noqa: S602 - shell command is repo-owned config
            step.shell or "", cwd=cwd, env=env, shell=True, check=False
        )

    if proc.returncode == 0:
        print(f"{_GREEN}PASS [{step.id}]{_RESET} {label}")
        return StepResult(step.id, "pass")
    print(f"{_RED}FAIL [{step.id}]{_RESET} {label} (exit {proc.returncode})")
    _print_fix_next(step)
    return StepResult(step.id, "fail", gating=not step.continue_on_error)


def _resolve_catalogue(ref: str) -> tuple[object, ...]:
    """Import ``module.path:attr`` and return the ``tuple[RuleEntry, ...]``."""
    module_path, _, attr = ref.partition(":")
    module = import_module(module_path)
    rules = getattr(module, attr)
    return tuple(rules)


def _run_catalogue_step(step: StepSpec, repo_root: Path, gate_id: str | None) -> StepResult:
    """Dispatch the consumer's RuleEntry catalogue via the shared runner.

    Runs IN-PROCESS through :func:`tc_fitness.runner.main_cli` so the runner's own
    ``run [id]`` / ``PASS`` / ``FAIL`` ledger prints inline. The step PASSes iff
    ``main_cli`` returns 0. A ``--gate <id>`` from the CLI is threaded through so
    ``tc-fitness run --gate F26`` runs exactly one catalogue rule.
    """
    # `kind == "catalogue"` guarantees `catalogue` is set (loader invariant).
    catalogue_ref = step.catalogue or ""
    label = step.summary or step.id
    print(f"{_YELLOW}run [{step.id}]{_RESET} {label}")

    repo_root_for_step = repo_root
    checks_dir = (repo_root / step.checks_dir).resolve() if step.checks_dir is not None else None

    # Make the catalogue module importable from the repo root (the consumer's
    # catalogue typically lives under scripts/checks/ alongside its checks).
    repo_root_str = str(repo_root)
    added = repo_root_str not in sys.path
    if added:
        sys.path.insert(0, repo_root_str)
    try:
        rules = _resolve_catalogue(catalogue_ref)
    except (ImportError, AttributeError) as exc:
        print(f"{_RED}FAIL [{step.id}]{_RESET} could not load catalogue {catalogue_ref!r}: {exc}")
        print(f'   fix: confirm the `catalogue = "module:attr"` ref resolves from {repo_root}')
        _print_fix_next(step)
        return StepResult(step.id, "fail", gating=not step.continue_on_error)
    finally:
        if added and repo_root_str in sys.path:
            sys.path.remove(repo_root_str)

    argv = ["--gate", gate_id] if gate_id else ["--all"]
    rc = main_cli(
        rules,  # type: ignore[arg-type]
        argv,
        repo_root=repo_root_for_step,
        checks_dir=checks_dir,
        dispatch=step.dispatch,
        parallel_subprocess=step.parallel,
    )
    if rc == 0:
        print(f"{_GREEN}PASS [{step.id}]{_RESET} {label}")
        return StepResult(step.id, "pass")
    print(f"{_RED}FAIL [{step.id}]{_RESET} {label} (exit {rc})")
    _print_fix_next(step)
    return StepResult(step.id, "fail", gating=not step.continue_on_error)


def _run_step(step: StepSpec, repo_root: Path, gate_id: str | None) -> StepResult:
    if step.kind == "catalogue":
        return _run_catalogue_step(step, repo_root, gate_id)
    return _run_command_step(step, repo_root)


def _print_aggregate(cfg: GateConfig, outcome: GateOutcome) -> None:
    print()
    if outcome.ok:
        print(f"{_GREEN}=== {cfg.name}: PASS ==={_RESET} ({outcome.ran} ran, {outcome.skipped} skipped)")
        return
    failures = outcome.gating_failures
    print(
        f"{_RED}=== {cfg.name}: FAIL ==={_RESET} "
        f"({len(failures)}/{outcome.ran} step(s) failed: {', '.join(failures)})"
    )
    print("   fix: address the FAIL step(s) above — each carries its own fix:/next:")
    print("   next: re-run `tc-fitness run` (or `tc-fitness run --only <id>` to retry one step)")


def run_gate(
    cfg: GateConfig,
    repo_root: Path,
    *,
    only: list[str] | None = None,
    gate_id: str | None = None,
) -> GateOutcome:
    """Run the configured steps in order; return the aggregate outcome.

    ``only`` restricts to the named step ids (in config order); ``gate_id`` is
    threaded into a catalogue step so a single fitness rule can be targeted.
    ``fail_fast`` (from the config) stops at the first gating failure.
    """
    print(f"=== {cfg.name} ===")
    if cfg.source is not None:
        print(f"    (config: {cfg.source})")

    selected = cfg.steps
    if only:
        wanted = set(only)
        selected = tuple(s for s in cfg.steps if s.id in wanted)
        unknown = wanted - {s.id for s in cfg.steps}
        if unknown:
            print(
                f"{_RED}unknown step id(s): {sorted(unknown)}{_RESET}; "
                f"fix: pass an id from {[s.id for s in cfg.steps]}"
            )

    outcome = GateOutcome()
    for step in selected:
        result = _run_step(step, repo_root, gate_id)
        outcome.results.append(result)
        if cfg.fail_fast and result.is_gating_failure:
            print(f"{_YELLOW}fail_fast: stopping at first gating failure ({step.id}){_RESET}")
            break

    _print_aggregate(cfg, outcome)
    return outcome


def main(argv: list[str] | None = None) -> int:
    """``tc-fitness`` console entrypoint.

    Subcommands:

    * ``run`` — load ``[tool.tc_fitness]`` and run the declared gate.
      ``--repo-root`` overrides CWD; ``--only ID`` runs a subset of steps;
      ``--gate ID`` targets a single fitness rule inside a catalogue step.
    """
    parser = argparse.ArgumentParser(
        prog="tc-fitness",
        description="The single runnable quality gate — local == CI by construction.",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    run_p = sub.add_parser("run", help="run the repo's declared [tool.tc_fitness] gate")
    run_p.add_argument(
        "--repo-root",
        default=".",
        help="repo root holding the gate config (default: CWD)",
    )
    run_p.add_argument(
        "--only",
        action="append",
        metavar="ID",
        help="run only the named step id(s) (repeatable)",
    )
    run_p.add_argument(
        "--gate",
        metavar="ID",
        help="target one fitness rule by id inside the catalogue step",
    )
    args = parser.parse_args(argv)

    repo_root = Path(args.repo_root).resolve()
    try:
        cfg = load_config(repo_root)
    except GateConfigError as exc:
        print(f"{_RED}FAIL tc-fitness run{_RESET} — {exc}", file=sys.stderr)
        return 2

    outcome = run_gate(cfg, repo_root, only=args.only, gate_id=args.gate)
    return outcome.exit_code


if __name__ == "__main__":
    sys.exit(main())


__all__ = [
    "GateOutcome",
    "StepResult",
    "main",
    "run_gate",
]
