"""Catalogue-driven fitness-function runner — the single dispatch surface.

A COMMON, repo-agnostic runner that both kairix and tc-agent-zone consume. It
makes a consumer's rule catalogue (a ``tuple[RuleEntry, ...]``) the single
source of truth: it reads the catalogue and DERIVES the invocation set, so a
repo's ``run-all.sh`` / pre-commit / CI all consume one declaration.

Thin-consumer API
-----------------
A repo's ``run_checks.py`` collapses to three lines::

    from tc_fitness.runner import main_cli
    from .catalogue import RULES
    raise SystemExit(main_cli(RULES))

…and a programmatic ``run(rules, *, mode, ...) -> Verdicts`` for tests and
embedding. Both accept injection hooks (``repo_root``, ``checks_dir``,
``scope_resolver``, ``enumeration_narrower``, ``paved_road_footer``,
``parallel_subprocess``) so a consumer can keep its exact prior behaviour.

Dispatch convention
-------------------
Each :class:`~tc_fitness.catalogue.RuleEntry` resolves to exactly one check
script:

* ``script`` unset → the default python check ``check_<check>.py``. These run
  IN-PROCESS: the runner imports the module and calls its ``main() -> int``
  inside a single process, sharing one
  :class:`~tc_fitness.context.CheckContext` whose AST cache parses every file
  at most once. The per-rule verdict is the check's own ``main()`` return code;
  a check that raises is isolated into a FAIL, never aborting the ledger.
* ``script`` set to a ``*.sh`` → run that real shell detector as a guarded
  subprocess. By default subprocesses run sequentially in catalogue order
  (byte-identical interleaving); pass ``parallel_subprocess=True`` to run them
  on a ThreadPoolExecutor (subprocess-IO bound) with buffered output replayed
  in registration order.
* A check declaring ``subprocess_arg_env`` (e.g. kairix's coverage check, which
  reads a Cobertura-XML path from an env var) runs as a subprocess too, with
  the resolved path appended; it is SKIPPED when the path is absent.

Modes
-----
* ``--all`` — every in-scope rule (dispatchable AND ``run_all``).
* ``--staged`` — precise per-rule selection against the staged paths
  (``git diff --cached --name-only``), single-sourced on each ``RuleEntry``
  (``staged_class`` / ``staged_scope``) and resolved by
  :mod:`tc_fitness.staged`. The hard invariant is no false negative on staged
  changes — when scope can't be resolved, the rule runs (fail-safe).
* ``--gate <id>`` — one rule by catalogue id.

Output contract (gate-runner discipline)
----------------------------------------
The runner prints a named ``run`` line and a ``PASS`` / ``FAIL`` verdict line
PER RULE, then a final aggregate verdict. Every subprocess is guarded — a check
that raises or exits non-zero is recorded as a FAIL for its rule, never
aborting the ledger. Exit code is non-zero iff any dispatched rule failed.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import contextlib
import importlib
import inspect
import io
import os
import subprocess
import sys
import traceback
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from tc_fitness.catalogue import RuleEntry, is_dispatchable
from tc_fitness.context import CheckContext
from tc_fitness.staged import (
    EnumerationNarrower,
    ScopeResolver,
    StagedDecision,
    decide,
    restrict_python_files,
)


class Colours:
    """The ANSI colour codes the named verdict ledger uses — a public namespace.

    Promoted to public API (v0.4.0) so a consumer that builds ledger lines by
    hand (taz) references ``Colours.GREEN`` etc. instead of importing the private
    ``_GREEN`` module constants. The values are byte-identical to the prior
    constants; the underscore names below remain as thin back-compat aliases.
    """

    RED = "\033[0;31m"
    GREEN = "\033[0;32m"
    YELLOW = "\033[0;33m"
    RESET = "\033[0m"


# Back-compat module-level aliases (kept until taz migrates off the private
# imports in Wave 4). They point at the public ``Colours`` values, so a single
# change to ``Colours`` is reflected everywhere.
_RED = Colours.RED
_GREEN = Colours.GREEN
_YELLOW = Colours.YELLOW
_RESET = Colours.RESET

_SHELL_SUFFIX = ".sh"

#: Default cap on parallel subprocess workers — subprocess-IO bound, so
#: threads are fine; capped to avoid over-saturating CI runners.
_DEFAULT_MAX_WORKERS = 8

# A paved-road footer hook: given a failing entry, return the affordance line
# to print under its FAIL verdict (or ``None`` to print nothing). Lets a
# consumer point a failing rule at its own query surface.
PavedRoadFooter = Callable[[RuleEntry], "str | None"]


@dataclass(frozen=True)
class ConditionalResult:
    """A conditional check's resolved runtime-arg decision.

    Returned by a :data:`ConditionalCheck` hook for a rule declaring
    ``subprocess_arg_env`` (e.g. kairix's coverage check). Either the check
    runs with ``extra_args`` appended, or it is skipped and ``skip_lines`` are
    printed verbatim — letting a consumer reproduce its EXACT skip text (the
    byte-identical-ledger contract).

    Exactly one of the two states applies:

    * ``run=True``  → dispatch the subprocess with ``extra_args`` appended.
    * ``run=False`` → skip (``None`` verdict); print each of ``skip_lines``.
    """

    run: bool
    extra_args: tuple[str, ...] = ()
    skip_lines: tuple[str, ...] = ()


# A conditional-check hook: given the entry, decide whether it runs (and with
# what runtime args) or is skipped (and what to print). When ``None``, the
# runner falls back to its built-in env-var resolution
# (``subprocess_arg_env`` / ``subprocess_arg_default``) with a generic skip
# line. A consumer supplies this to reproduce its exact skip text.
ConditionalCheck = Callable[[RuleEntry], "ConditionalResult | None"]


#: A per-entry skip-line builder: given the :class:`RuleEntry`, return the exact
#: lines to print on a skip. Lets a consumer interpolate ``entry.id`` (and any
#: other field) so two rules SHARING one script — kairix's F7/F9, both
#: ``check_per_file_coverage.py`` — emit DISTINCT ``skip [F7]`` / ``skip [F9]``
#: ledgers instead of one static tuple's identical text.
SkipLineFn = Callable[[RuleEntry], tuple[str, ...]]


def make_env_path_conditional_check(
    *,
    env_var: str,
    default_rel: str,
    repo_root: Path,
    force_skip: Callable[[], bool] | None = None,
    force_skip_lines: tuple[str, ...] = (),
    absent_skip_lines: tuple[str, ...] = (),
    force_skip_line_fn: SkipLineFn | None = None,
    absent_skip_line_fn: SkipLineFn | None = None,
) -> ConditionalCheck:
    """Build a :data:`ConditionalCheck` that resolves a runtime-arg PATH.

    Generalises kairix's ``_make_conditional_check`` + ``_coverage_xml_path``
    into declarative config. The returned hook, given a rule:

    1. when ``force_skip`` is supplied and returns ``True`` → skip with
       ``force_skip_line_fn(entry)`` if given, else ``force_skip_lines``
       (kairix's ``--skip-coverage`` path), regardless of whether the path
       exists;
    2. resolve the path from ``env_var`` (when set + non-empty), else
       ``repo_root / default_rel``; if it does not exist → skip with
       ``absent_skip_line_fn(entry)`` if given, else ``absent_skip_lines`` (the
       "report not found" path);
    3. otherwise → run with the resolved absolute path appended as a single
       extra arg.

    Per-entry skip text (the byte-identity ledger for shared-script rules)
    --------------------------------------------------------------------
    The static tuples are fixed at factory-build time, so two rules that share
    ONE script and differ only by ``entry.id`` — kairix's F7/F9, both
    ``check_per_file_coverage.py`` — would emit IDENTICAL skip text where kairix
    emits distinct ``skip [F7]`` / ``skip [F9]`` lines. The ``*_skip_line_fn``
    callables receive the :class:`RuleEntry` and interpolate per id, so the
    factory reproduces kairix's seam (``skip [{entry.id}] {resolve_script(entry)}
    — …``) byte-for-byte. **Precedence: the ``*_line_fn`` wins when provided**;
    the static tuple remains for the single-rule case (back-compat).

    The env-var name, default relative path, force predicate, and BOTH skip-line
    surfaces are CONFIG, so a consumer reproduces its EXACT skip text. Nothing
    kairix-specific is baked in.

    Args:
        env_var: environment variable carrying the path (wins when set).
        default_rel: repo-relative default path used when ``env_var`` is unset.
        repo_root: root the ``default_rel`` resolves under.
        force_skip: zero-arg predicate; ``True`` forces a skip (bound to a flag
            like ``--skip-coverage``). ``None`` disables the force branch.
        force_skip_lines: the exact lines printed on a forced skip (static).
        absent_skip_lines: the exact lines printed when the path is absent
            (static).
        force_skip_line_fn: per-entry forced-skip line builder; wins over
            ``force_skip_lines`` when given.
        absent_skip_line_fn: per-entry absent-skip line builder; wins over
            ``absent_skip_lines`` when given.
    """

    def _hook(entry: RuleEntry) -> ConditionalResult:
        if force_skip is not None and force_skip():
            lines = force_skip_line_fn(entry) if force_skip_line_fn is not None else force_skip_lines
            return ConditionalResult(run=False, skip_lines=lines)
        env_value = os.environ.get(env_var)
        candidate = Path(env_value) if env_value else (repo_root / default_rel)
        if not candidate.exists():
            lines = absent_skip_line_fn(entry) if absent_skip_line_fn is not None else absent_skip_lines
            return ConditionalResult(run=False, skip_lines=lines)
        return ConditionalResult(run=True, extra_args=(str(candidate),))

    return _hook


@dataclass
class Verdicts:
    """The aggregate result of a run — the programmatic return of :func:`run`.

    Attributes:
        ran: how many rules actually dispatched a verdict.
        skipped: how many rules were intentionally skipped (out of staged
            scope, or a conditional check whose input was absent).
        failures: the ids of the rules that failed, in dispatch order.
    """

    ran: int = 0
    skipped: int = 0
    failures: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        """True iff no dispatched rule failed."""
        return not self.failures

    @property
    def exit_code(self) -> int:
        """0 when clean, 1 when any rule failed."""
        return 0 if self.ok else 1


@dataclass
class RunnerConfig:
    """Resolved runner configuration — the injection seams a consumer sets.

    Every field has a sensible default so a bare ``main_cli(RULES)`` works, but
    a consumer overrides them to keep its exact prior behaviour:

    * ``repo_root`` — the repository root (default: CWD). Baselines, scopes,
      and the staged ``git`` call resolve against it.
    * ``checks_dir`` — where ``check_*.py`` / ``*.sh`` scripts live (default:
      ``<repo_root>/scripts/checks``). Put on ``sys.path`` so the in-process
      ``importlib.import_module`` resolves sibling check modules.
    * ``scope_resolver`` — derives a rule's staged scope from its check script
      when ``staged_scope`` is unset (the consumer's FitnessRule-aware hook).
    * ``enumeration_narrower`` — a consumer's extra file-index narrowing for
      file-local staged runs, layered on top of the package-level
      ``tc_fitness.python_files`` narrowing.
    * ``paved_road_footer`` — the affordance line printed under a FAIL.
    * ``conditional_check`` — governs a ``subprocess_arg_env`` rule's runtime
      arg + exact skip text (the consumer reproduces its own skip lines).
    * ``parallel_subprocess`` — run ``.sh`` subprocess checks on a thread pool
      (default ``False`` → sequential, byte-identical interleaving).
    * ``max_workers`` — thread-pool cap when parallel.
    """

    repo_root: Path = field(default_factory=Path.cwd)
    checks_dir: Path | None = None
    scope_resolver: ScopeResolver | None = None
    enumeration_narrower: EnumerationNarrower | None = None
    paved_road_footer: PavedRoadFooter | None = None
    conditional_check: ConditionalCheck | None = None
    parallel_subprocess: bool = False
    max_workers: int = _DEFAULT_MAX_WORKERS
    #: Dispatch strategy for pure-python checks. ``"inprocess"`` (default,
    #: v0.3.0 behaviour) imports the module and calls ``main()`` in-process,
    #: sharing one ``CheckContext`` AST cache. ``"subprocess"`` routes EVERY
    #: check — python included — through the guarded subprocess path, so a
    #: consumer with no shared-context requirement (taz) can drop a hand-rolled
    #: subprocess dispatch. ``.sh`` detectors run as subprocesses either way.
    dispatch: str = "inprocess"

    def __post_init__(self) -> None:
        self.repo_root = self.repo_root.resolve()
        if self.checks_dir is None:
            self.checks_dir = self.repo_root / "scripts" / "checks"
        # Make the checks dir importable for in-process dispatch.
        checks_dir = str(self.checks_dir)
        if checks_dir not in sys.path:
            sys.path.insert(0, checks_dir)


# ── resolution helpers ───────────────────────────────────────────────────


def resolve_script(entry: RuleEntry) -> str:
    """Return the check-script filename for ``entry`` — the ``script``
    override, or the default ``check_<check>.py``."""
    if entry.script:
        return entry.script
    return f"check_{entry.check}.py"


def _dispatches_in_process(entry: RuleEntry) -> bool:
    """True iff ``entry``'s check runs in-process (pure-python, no runtime
    arg). A ``.sh`` script or a check declaring a ``subprocess_arg_env`` runs
    as a guarded subprocess instead."""
    if entry.subprocess_arg_env is not None:
        return False
    return not resolve_script(entry).endswith(_SHELL_SUFFIX)


def _runs_in_process(entry: RuleEntry, cfg: RunnerConfig) -> bool:
    """Whether ``entry`` runs in-process, honouring ``cfg.dispatch``.

    ``dispatch="subprocess"`` forces EVERY check — python included — onto the
    guarded subprocess path (taz's pure-consumer mode). Otherwise the v0.3.0
    per-entry rule (:func:`_dispatches_in_process`) applies."""
    if cfg.dispatch == "subprocess":
        return False
    return _dispatches_in_process(entry)


def _conditional_arg_path(entry: RuleEntry, cfg: RunnerConfig) -> Path | None:
    """The runtime-arg path for a conditional subprocess check, or ``None`` to
    skip. Reads ``entry.subprocess_arg_env`` from the environment, falling back
    to ``subprocess_arg_default`` resolved under the repo root; skips when the
    resolved path does not exist."""
    if entry.subprocess_arg_env is None:
        return None
    env_path = os.environ.get(entry.subprocess_arg_env)
    if env_path:
        candidate = Path(env_path)
    elif entry.subprocess_arg_default:
        candidate = cfg.repo_root / entry.subprocess_arg_default
    else:
        return None
    return candidate if candidate.exists() else None


# ── per-rule dispatch ────────────────────────────────────────────────────


def _load_check_main(script: str) -> Callable[[], int]:
    """Import the check module for ``script`` and return a zero-arg callable
    that invokes its ``main``.

    Some checks declare ``main(argv: list[str] | None = None)`` and default to
    ``argparse``'s ``parse_args(None)`` — which reads ``sys.argv``. In-process,
    ``sys.argv`` is the RUNNER's flags, which the check's parser would reject.
    So when ``main`` accepts an ``argv`` parameter we pass an explicit empty
    list — reproducing the no-arguments subprocess invocation."""
    module_name = script[: -len(".py")]
    module = importlib.import_module(module_name)
    main_fn = module.main
    accepts_argv = bool(inspect.signature(main_fn).parameters)

    def _invoke() -> int:
        result = main_fn([]) if accepts_argv else main_fn()
        return int(result)

    return _invoke


def _print_paved_road(entry: RuleEntry, cfg: RunnerConfig) -> None:
    """Print the consumer's paved-road affordance under a FAIL, if any."""
    if cfg.paved_road_footer is None:
        return
    footer = cfg.paved_road_footer(entry)
    if footer:
        print(footer)


def _run_one_inprocess(entry: RuleEntry, cfg: RunnerConfig) -> int:
    """Dispatch ``entry``'s pure-python check IN-PROCESS, sharing the context.

    Prints the ``run`` / ``PASS`` / ``FAIL`` framing the subprocess path prints,
    with the check's own stdout/stderr replayed inline between them. The check
    is fully isolated: its ``main()`` is called inside a try/except over
    ``BaseException``; a raised exception OR a ``SystemExit`` is converted to a
    FAIL with the traceback, exactly as a non-zero subprocess exit would have
    been — one crashing check never aborts the ledger.
    """
    script = resolve_script(entry)
    print(f"{_YELLOW}run [{entry.id}]{_RESET} {script}")

    out_buf = io.StringIO()
    err_buf = io.StringIO()
    crashed = False
    rc = 1
    try:
        check_main = _load_check_main(script)
        with contextlib.redirect_stdout(out_buf), contextlib.redirect_stderr(err_buf):
            result = check_main()
        rc = result if isinstance(result, int) else 1
    except BaseException:
        # Isolation boundary: one check must never abort the ledger. Every
        # failure mode — a raised exception, a SystemExit, a KeyboardInterrupt
        # bubbling out of a check's main() — is converted to a FAIL verdict,
        # exactly as a non-zero subprocess exit would have been.
        crashed = True
        traceback.print_exc(file=err_buf)
        rc = 1

    captured_out = out_buf.getvalue()
    if captured_out:
        sys.stdout.write(captured_out)
    captured_err = err_buf.getvalue()
    if captured_err:
        sys.stderr.write(captured_err)

    if rc == 0:
        print(f"{_GREEN}PASS [{entry.id}]{_RESET} {entry.summary[:88]}")
        return 0
    suffix = "" if crashed else f" (exit {rc})"
    print(f"{_RED}FAIL [{entry.id}]{_RESET} {entry.summary[:88]}{suffix}")
    _print_paved_road(entry, cfg)
    return 1


@dataclass(frozen=True)
class _Built:
    """Resolved subprocess invocation, or a skip with verbatim lines."""

    argv: list[str] | None = None
    script_path: Path | None = None
    skip_lines: tuple[str, ...] = ()


def _resolve_conditional(entry: RuleEntry, cfg: RunnerConfig) -> ConditionalResult:
    """The conditional decision for a ``subprocess_arg_env`` rule.

    Prefers the consumer's ``conditional_check`` hook (so its exact skip text is
    reproduced); falls back to built-in env-var resolution with a generic skip
    line."""
    if cfg.conditional_check is not None:
        decided = cfg.conditional_check(entry)
        if decided is not None:
            return decided
    arg_path = _conditional_arg_path(entry, cfg)
    if arg_path is None:
        return ConditionalResult(run=False, skip_lines=(_generic_skip_line(entry),))
    return ConditionalResult(run=True, extra_args=(str(arg_path),))


def _generic_skip_line(entry: RuleEntry) -> str:
    return f"{_YELLOW}skip [{entry.id}]{_RESET} {resolve_script(entry)} — runtime input not found"


def _argv_extra_args(entry: RuleEntry) -> list[str]:
    """The declarative argv-exception args for ``entry`` (v0.4.0).

    ``static_extra_args`` (always) followed by each ``env_gated_extra_args``
    pair's ``arg`` whose ``env_var`` is set — in declaration order. Default-safe:
    an entry with neither yields ``[]`` (the v0.3.0 argv)."""
    extra: list[str] = list(entry.static_extra_args)
    for env_var, arg in entry.env_gated_extra_args:
        if os.environ.get(env_var):
            extra.append(arg)
    return extra


def _subprocess_argv(entry: RuleEntry, cfg: RunnerConfig) -> _Built:
    """Build the argv + script path for ``entry``'s subprocess, or a skip."""
    script = resolve_script(entry)
    assert cfg.checks_dir is not None
    if entry.script_path_override is not None:
        # The script lives OUTSIDE the checks dir — resolve under the repo root.
        script_path = cfg.repo_root / entry.script_path_override
    else:
        script_path = cfg.checks_dir / script
    interpreter = "bash" if script.endswith(_SHELL_SUFFIX) else sys.executable

    extra_args: list[str] = []
    if entry.subprocess_arg_env is not None:
        decided = _resolve_conditional(entry, cfg)
        if not decided.run:
            return _Built(skip_lines=decided.skip_lines)
        extra_args = list(decided.extra_args)

    extra_args.extend(_argv_extra_args(entry))

    return _Built(argv=[interpreter, str(script_path), *extra_args], script_path=script_path)


def _run_one_subprocess(entry: RuleEntry, cfg: RunnerConfig) -> int | None:
    """Dispatch ``entry``'s check as a guarded subprocess (sequential path).

    Print a named ``run`` line and a ``PASS`` / ``FAIL`` verdict. Return 0 on
    pass, 1 on fail (including a missing script or a crashing check), or
    ``None`` when the rule was intentionally skipped (conditional input
    absent).
    """
    script = resolve_script(entry)
    built = _subprocess_argv(entry, cfg)
    if built.argv is None:
        for line in built.skip_lines:
            print(line)
        return None
    argv = built.argv
    script_path = built.script_path
    assert script_path is not None

    print(f"{_YELLOW}run [{entry.id}]{_RESET} {script}")

    if not script_path.exists():
        rel = script_path.name
        print(f"{_RED}FAIL [{entry.id}]{_RESET} — check script not found: {rel}")
        print("   fix: restore the script or correct the catalogue entry's check/script field.")
        return 1

    try:
        result = subprocess.run(argv, cwd=cfg.repo_root, check=False)
        rc = result.returncode
    except OSError as exc:
        print(f"{_RED}FAIL [{entry.id}]{_RESET} — could not launch {script}: {exc}")
        return 1

    if rc == 0:
        print(f"{_GREEN}PASS [{entry.id}]{_RESET} {entry.summary[:88]}")
        return 0
    print(f"{_RED}FAIL [{entry.id}]{_RESET} {entry.summary[:88]} (exit {rc})")
    _print_paved_road(entry, cfg)
    return 1


def _capture_one_subprocess(
    entry: RuleEntry, cfg: RunnerConfig
) -> tuple[int | None, str, str, tuple[str, ...]]:
    """Run ``entry``'s subprocess with output CAPTURED — no direct-fd race.

    The capturing primitive shared by the ``--all`` parallel path and the
    ``--staged`` path: build the argv, run the child with ``capture_output=True``
    (so its stdout/stderr land in pipes the parent owns, never on the inherited
    fd1 where they would race the parent's buffered ``print()`` under
    redirection), and return ``(verdict, stdout, stderr, skip_lines)``. The
    CALLER replays the named ledger via :func:`_replay_subprocess_verdict`, so
    the report stays byte-stable. ``verdict`` is 0 pass / 1 fail / ``None`` skip
    (conditional input absent → ``skip_lines`` carry the verbatim skip text).
    """
    built = _subprocess_argv(entry, cfg)
    if built.argv is None:
        return None, "", "", built.skip_lines
    argv = built.argv
    script_path = built.script_path
    assert script_path is not None
    if not script_path.exists():
        return 1, "", f"check script not found: {script_path.name}", ()
    try:
        result = subprocess.run(argv, cwd=cfg.repo_root, capture_output=True, text=True, check=False)
    except OSError as exc:
        return 1, "", f"could not launch {script_path.name}: {exc}", ()
    return result.returncode, (result.stdout or ""), (result.stderr or ""), ()


def _replay_subprocess_verdict(
    entry: RuleEntry,
    cfg: RunnerConfig,
    rc: int | None,
    out: str,
    err: str,
    skip_lines: tuple[str, ...],
) -> int | None:
    """Replay one captured subprocess result as the named ledger, in order.

    The single replay surface both the parallel ``--all`` path and the staged
    path use, so a captured subprocess emits the IDENTICAL ``run [id]`` →
    captured output → ``PASS``/``FAIL [id]`` framing regardless of which mode
    dispatched it. A ``None`` ``rc`` is an intentional skip (print the verbatim
    ``skip_lines``); otherwise the child's captured stdout/stderr are written
    between the run and verdict lines (newline-normalised), then the verdict.
    """
    if rc is None:
        for line in skip_lines:
            print(line)
        return None
    print(f"{_YELLOW}run [{entry.id}]{_RESET} {resolve_script(entry)}")
    if out:
        sys.stdout.write(out if out.endswith("\n") else out + "\n")
    if err:
        sys.stderr.write(err if err.endswith("\n") else err + "\n")
    if rc == 0:
        print(f"{_GREEN}PASS [{entry.id}]{_RESET} {entry.summary[:88]}")
        return 0
    print(f"{_RED}FAIL [{entry.id}]{_RESET} {entry.summary[:88]} (exit {rc})")
    _print_paved_road(entry, cfg)
    return 1


def _run_one_subprocess_capturing(entry: RuleEntry, cfg: RunnerConfig) -> int | None:
    """Dispatch one subprocess check via the CAPTURING path (buffer + replay).

    The staged-mode analogue of :func:`_run_one_subprocess`, with the
    byte-stable behaviour the ``--all`` parallel path already has: the child's
    output is captured (never leaks to the inherited fd1) and replayed in order
    inside the rule's ``run``/verdict framing. Returns 0 pass / 1 fail / ``None``
    skip, exactly as the non-capturing variant did — only the FORMAT stabilises.
    """
    rc, out, err, skip_lines = _capture_one_subprocess(entry, cfg)
    return _replay_subprocess_verdict(entry, cfg, rc, out, err, skip_lines)


# ── selection ────────────────────────────────────────────────────────────


def select_all(rules: tuple[RuleEntry, ...]) -> list[RuleEntry]:
    """In-scope rules for ``--all``: dispatchable AND ``run_all``.

    Public (v0.4.0) so a consumer building its own dispatch loop selects the
    same set the runner does, instead of importing the private ``_select_all``.
    """
    return [e for e in rules if is_dispatchable(e) and e.run_all]


def select_gate(rules: tuple[RuleEntry, ...], gate_id: str) -> list[RuleEntry]:
    """Rules whose catalogue ``id`` matches ``gate_id`` (case-insensitive).

    Public (v0.4.0); the ``--gate <id>`` selector a consumer can reuse."""
    return [e for e in rules if e.id.lower() == gate_id.lower() and is_dispatchable(e)]


# Back-compat private aliases (kept until taz migrates off the private imports).
_select_all = select_all
_select_gate = select_gate


def _staged_decisions(
    rules: tuple[RuleEntry, ...],
    staged: list[str],
    cfg: RunnerConfig,
) -> list[tuple[RuleEntry, StagedDecision]]:
    """Per-rule staged decision for every ``--all`` entry, in catalogue order,
    deduped by resolved script (the same script runs once)."""
    out: list[tuple[RuleEntry, StagedDecision]] = []
    seen_scripts: set[str] = set()
    for entry in _select_all(rules):
        script = resolve_script(entry)
        if script in seen_scripts:
            continue
        seen_scripts.add(script)
        out.append((entry, decide(entry, script, staged, cfg.scope_resolver)))
    return out


# ── parallel subprocess dispatch (taz normalisation) ─────────────────────


def _run_subprocess_parallel(
    entries: list[RuleEntry],
    cfg: RunnerConfig,
) -> dict[str, int | None]:
    """Run the given subprocess ``entries`` on a thread pool, capturing output.

    Subprocess-IO bound, so threads are fine (the GIL is released while waiting
    on child processes). Returns ``id -> verdict`` (0 pass / 1 fail / ``None``
    skip). The CALLER replays the named ledger in catalogue order so the report
    stays byte-stable regardless of completion order.
    """
    captured: dict[str, tuple[int | None, str, str, tuple[str, ...]]] = {}

    def _work(entry: RuleEntry) -> tuple[str, int | None, str, str, tuple[str, ...]]:
        rc, out, err, skip_lines = _capture_one_subprocess(entry, cfg)
        return entry.id, rc, out, err, skip_lines

    workers = min(cfg.max_workers, max(1, len(entries)))
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(_work, e) for e in entries]
        for future in concurrent.futures.as_completed(futures):
            rid, rc, out, err, skip_lines = future.result()
            captured[rid] = (rc, out, err, skip_lines)

    verdicts: dict[str, int | None] = {}
    for entry in entries:
        rc, out, err, skip_lines = captured[entry.id]
        verdicts[entry.id] = _replay_subprocess_verdict(entry, cfg, rc, out, err, skip_lines)
    return verdicts


# ── dispatch loops ───────────────────────────────────────────────────────


def _dispatch(entries: list[RuleEntry], cfg: RunnerConfig) -> Verdicts:
    """Run every entry once per distinct resolved script (dedup), in catalogue
    order. Aggregate ledger. Pure-python rules run IN-PROCESS sharing one
    :class:`CheckContext`; shell / conditional checks run as guarded
    subprocesses (sequentially, or in parallel when ``parallel_subprocess``)."""
    seen_scripts: set[str] = set()
    deduped: list[RuleEntry] = []
    for entry in entries:
        script = resolve_script(entry)
        if script in seen_scripts:
            continue
        seen_scripts.add(script)
        deduped.append(entry)

    verdict = Verdicts()
    parallel_verdicts: dict[str, int | None] = {}
    if cfg.parallel_subprocess:
        subproc_entries = [e for e in deduped if not _runs_in_process(e, cfg)]
        if subproc_entries:
            parallel_verdicts = _run_subprocess_parallel(subproc_entries, cfg)

    ctx = CheckContext(repo_root=cfg.repo_root)
    with ctx.install():
        for entry in deduped:
            if _runs_in_process(entry, cfg):
                result: int | None = _run_one_inprocess(entry, cfg)
            elif cfg.parallel_subprocess:
                result = parallel_verdicts.get(entry.id)
            else:
                result = _run_one_subprocess(entry, cfg)
            if result is None:
                verdict.skipped += 1
                continue
            verdict.ran += 1
            if result != 0:
                verdict.failures.append(entry.id)

    _print_aggregate(verdict)
    return verdict


def _run_staged_one(
    entry: RuleEntry,
    cfg: RunnerConfig,
    decision: StagedDecision,
    staged: list[str],
) -> int | None:
    """Dispatch one RUN-decided rule in staged mode.

    File-local rules carrying a staged file subset run inside the package-level
    :func:`restrict_python_files` (and the consumer's extra
    ``enumeration_narrower``, if any) so the in-process check walks ONLY those
    files. Everything else runs over its full natural scope.

    Subprocess checks route through the CAPTURING dispatch
    (:func:`_run_one_subprocess_capturing`) — the same buffer-and-replay path
    ``--all`` uses — so a child detector's stdout never races the parent's
    buffered ledger on the inherited fd1, and staged output stays byte-stable.
    """
    if not _runs_in_process(entry, cfg):
        return _run_one_subprocess_capturing(entry, cfg)
    if decision.scope_files:
        scope_files = list(decision.scope_files)
        with contextlib.ExitStack() as stack:
            stack.enter_context(restrict_python_files(cfg.repo_root, scope_files))
            if cfg.enumeration_narrower is not None:
                stack.enter_context(cfg.enumeration_narrower(cfg.repo_root, scope_files))
            return _run_one_inprocess(entry, cfg)
    return _run_one_inprocess(entry, cfg)


def _dispatch_staged(
    rules: tuple[RuleEntry, ...],
    staged: list[str],
    cfg: RunnerConfig,
) -> Verdicts:
    """Precise staged dispatch. SKIPPED rules print a transparent
    ``skip [id] — <reason>`` line (auditable, never silent); RUN rules dispatch
    exactly as ``--all`` does."""
    decisions = _staged_decisions(rules, staged, cfg)
    verdict = Verdicts()
    ctx = CheckContext(repo_root=cfg.repo_root)
    with ctx.install():
        for entry, decision in decisions:
            if not decision.run:
                print(f"{_YELLOW}skip [{entry.id}]{_RESET} {resolve_script(entry)} — {decision.reason}")
                verdict.skipped += 1
                continue
            result = _run_staged_one(entry, cfg, decision, staged)
            if result is None:
                verdict.skipped += 1
                continue
            verdict.ran += 1
            if result != 0:
                verdict.failures.append(entry.id)

    print()
    print(
        f"{_YELLOW}staged selection:{_RESET} {verdict.ran} ran, {verdict.skipped} skipped "
        "(not in staged scope or report absent)"
    )
    if verdict.failures:
        print(
            f"{_RED}=== Architecture fitness functions FAILED ==={_RESET} "
            f"({len(verdict.failures)}/{verdict.ran} rule(s) failed: {', '.join(verdict.failures)})"
        )
    else:
        print(f"{_GREEN}=== All {verdict.ran} staged architecture fitness functions passed ==={_RESET}")
    return verdict


def print_aggregate(verdict: Verdicts) -> None:
    """Print the final aggregate verdict line for an ``--all`` / ``--gate``
    dispatch (byte-identical to kairix's runner).

    Public (v0.4.0) so a consumer that runs its own dispatch loop emits the
    SAME aggregate banner the runner does, instead of importing the private
    ``_print_aggregate``."""
    print()
    if verdict.failures:
        print(
            f"{Colours.RED}=== Architecture fitness functions FAILED ==={Colours.RESET} "
            f"({len(verdict.failures)}/{verdict.ran} rule(s) failed: {', '.join(verdict.failures)})"
        )
    else:
        print(f"{Colours.GREEN}=== All {verdict.ran} architecture fitness functions passed ==={Colours.RESET}")


# Back-compat private alias (kept until taz migrates off the private import).
_print_aggregate = print_aggregate


# ── git staged paths ─────────────────────────────────────────────────────


def staged_paths(repo_root: Path) -> list[str]:
    """``git diff --cached --name-only`` — staged file paths, repo-relative.

    Guarded: a git failure returns an empty list, which the caller treats as
    "run everything" (fail-safe)."""
    try:
        result = subprocess.run(
            ["git", "diff", "--cached", "--name-only"],
            cwd=repo_root,
            capture_output=True,
            text=True,
            check=False,
        )
    except FileNotFoundError:
        return []
    if result.returncode != 0:
        return []
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


# ── programmatic + CLI entrypoints ───────────────────────────────────────


def run(
    rules: tuple[RuleEntry, ...],
    *,
    mode: str = "all",
    gate_id: str | None = None,
    staged_files: list[str] | None = None,
    repo_root: Path | None = None,
    checks_dir: Path | None = None,
    scope_resolver: ScopeResolver | None = None,
    enumeration_narrower: EnumerationNarrower | None = None,
    paved_road_footer: PavedRoadFooter | None = None,
    conditional_check: ConditionalCheck | None = None,
    parallel_subprocess: bool = False,
    max_workers: int = _DEFAULT_MAX_WORKERS,
    dispatch: str = "inprocess",
) -> Verdicts:
    """Run ``rules`` in ``mode`` and return the :class:`Verdicts`.

    ``mode`` is ``"all"`` (default), ``"staged"`` (pass ``staged_files`` to
    override the ``git`` call), or ``"gate"`` (with ``gate_id``). ``dispatch`` is
    ``"inprocess"`` (default, v0.3.0) or ``"subprocess"`` (route every check
    through the guarded subprocess path). The injection kwargs map onto
    :class:`RunnerConfig`. Always prints the named verdict ledger; the return
    value carries the structured outcome for embedders."""
    cfg = RunnerConfig(
        repo_root=repo_root if repo_root is not None else Path.cwd(),
        checks_dir=checks_dir,
        scope_resolver=scope_resolver,
        enumeration_narrower=enumeration_narrower,
        paved_road_footer=paved_road_footer,
        conditional_check=conditional_check,
        parallel_subprocess=parallel_subprocess,
        max_workers=max_workers,
        dispatch=dispatch,
    )

    if mode == "gate":
        if not gate_id:
            raise ValueError("mode='gate' requires gate_id")
        entries = _select_gate(rules, gate_id)
        if not entries:
            print(f"{_RED}no catalogue rule with id {gate_id!r}{_RESET}")
            print("   fix: pass a real id (see your catalogue) or run --all.")
            v = Verdicts(failures=["<no-such-gate>"])
            return v
        print(f"=== Architecture fitness function: {gate_id} ===")
        return _dispatch(entries, cfg)

    if mode == "staged":
        staged = staged_files if staged_files is not None else staged_paths(cfg.repo_root)
        print("=== Architecture fitness functions (staged) ===")
        return _dispatch_staged(rules, staged, cfg)

    print("=== Architecture fitness functions ===")
    return _dispatch(_select_all(rules), cfg)


def main_cli(
    rules: tuple[RuleEntry, ...],
    argv: list[str] | None = None,
    *,
    prog: str = "run_checks.py",
    repo_root: Path | None = None,
    checks_dir: Path | None = None,
    scope_resolver: ScopeResolver | None = None,
    enumeration_narrower: EnumerationNarrower | None = None,
    paved_road_footer: PavedRoadFooter | None = None,
    conditional_check: ConditionalCheck | None = None,
    parallel_subprocess: bool = False,
    max_workers: int = _DEFAULT_MAX_WORKERS,
    dispatch: str = "inprocess",
    extra_flags: Sequence[tuple[str, dict[str, object]]] = (),
    post_parse: Callable[[argparse.Namespace], dict[str, object]] | None = None,
) -> int:
    """Parse ``--all`` / ``--staged`` / ``--gate`` and dispatch ``rules``.

    The thin-consumer entrypoint: a repo's ``run_checks.py`` is

    .. code-block:: python

        from tc_fitness.runner import main_cli
        from .catalogue import RULES
        raise SystemExit(main_cli(RULES))

    Consumer-specific flags retire a forked ``main()``: ``extra_flags`` is a
    sequence of ``(flag, argparse-add_argument-kwargs)`` added to the parser
    (e.g. ``[("--skip-coverage", {"action": "store_true"})]``), and ``post_parse``
    maps the parsed :class:`argparse.Namespace` to a dict of EXTRA ``run()``
    kwargs (e.g. a ``conditional_check`` built from the flag value). The
    post-parse dict overrides the corresponding ``common`` entries, so a
    consumer threads its flag into any seam without subclassing the parser.

    Returns the process exit code (0 clean, 1 any failure, 2 unknown gate id).
    """
    parser = argparse.ArgumentParser(
        prog=prog,
        description="Catalogue-driven fitness-function runner (tc_fitness.runner).",
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--all", action="store_true", help="run every in-scope rule (default)")
    group.add_argument("--staged", action="store_true", help="run only rules a staged change could trip")
    group.add_argument("--gate", metavar="ID", help="run one rule by catalogue id (e.g. F26)")
    for flag, kwargs in extra_flags:
        parser.add_argument(flag, **kwargs)  # type: ignore[arg-type]
    args = parser.parse_args(argv)

    common: dict[str, object] = {
        "repo_root": repo_root,
        "checks_dir": checks_dir,
        "scope_resolver": scope_resolver,
        "enumeration_narrower": enumeration_narrower,
        "paved_road_footer": paved_road_footer,
        "conditional_check": conditional_check,
        "parallel_subprocess": parallel_subprocess,
        "max_workers": max_workers,
        "dispatch": dispatch,
    }
    if post_parse is not None:
        common.update(post_parse(args))

    if args.gate:
        verdict = run(rules, mode="gate", gate_id=args.gate, **common)  # type: ignore[arg-type]
        if verdict.failures == ["<no-such-gate>"]:
            return 2
        return verdict.exit_code

    if args.staged:
        return run(rules, mode="staged", **common).exit_code  # type: ignore[arg-type]

    return run(rules, mode="all", **common).exit_code  # type: ignore[arg-type]


__all__ = [
    "Colours",
    "Verdicts",
    "RunnerConfig",
    "PavedRoadFooter",
    "ConditionalCheck",
    "ConditionalResult",
    "SkipLineFn",
    "make_env_path_conditional_check",
    "resolve_script",
    "staged_paths",
    "select_all",
    "select_gate",
    "print_aggregate",
    "run",
    "main_cli",
]
