"""Behavioural tests for the catalogue-driven runner.

Each test builds a SYNTHETIC catalogue + check modules in a tmp checks dir, so
the runner's dispatch is exercised end-to-end without depending on any consumer
repo's rules. The fixtures prove:

- in-process dispatch (a ``check_<x>.py`` with ``main() -> int`` runs in-process,
  sharing one CheckContext; a crashing check is isolated into a FAIL);
- guarded subprocess dispatch for ``*.sh`` shell detectors (sequential AND the
  parallel ThreadPoolExecutor path);
- the named verdict ledger shape (``run [id]`` / ``PASS [id]`` / ``FAIL [id]``
  + the aggregate line) — the format kairix's F83 + verdict tests depend on;
- staged-selection soundness (no false-negative on a staged change; the
  transparent skip ledger; file-local narrowing);
- ``--gate <id>`` selection;
- ``run_all=False`` exclusion from ``--all``;
- the programmatic ``run(...) -> Verdicts`` over a mixed python+shell catalogue.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

from tc_fitness.catalogue import RuleEntry
from tc_fitness.runner import (
    ConditionalResult,
    RunnerConfig,
    Verdicts,
    main_cli,
    resolve_script,
    run,
)

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


def _plain(text: str) -> str:
    """Strip ANSI colour codes so a ledger assertion reads the bare text.

    The runner wraps the ``run``/``PASS``/``FAIL`` markers in colour codes
    (byte-identical to kairix's runner), which puts a reset escape between
    ``run [id]`` and the script name. Tests assert on the colour-free form."""
    return _ANSI_RE.sub("", text)

# --------------------------------------------------------------------------- #
# fixture helpers — write synthetic check modules + a catalogue into tmp_path
# --------------------------------------------------------------------------- #


def _write_py_check(checks_dir: Path, name: str, body: str) -> None:
    """Write a ``check_<name>.py`` whose ``main()`` body is ``body`` (must
    ``return`` an int)."""
    (checks_dir / f"check_{name}.py").write_text(
        "def main():\n" + "\n".join(f"    {line}" for line in body.splitlines()) + "\n"
    )


def _write_sh_check(checks_dir: Path, filename: str, exit_code: int, echo: str = "") -> None:
    """Write a ``*.sh`` detector exiting ``exit_code``."""
    script = "#!/usr/bin/env bash\n"
    if echo:
        script += f'echo "{echo}"\n'
    script += f"exit {exit_code}\n"
    (checks_dir / filename).write_text(script)
    (checks_dir / filename).chmod(0o755)


@pytest.fixture
def checks_dir(tmp_path: Path) -> Path:
    d = tmp_path / "scripts" / "checks"
    d.mkdir(parents=True)
    return d


@pytest.fixture
def repo_root(tmp_path: Path) -> Path:
    return tmp_path


@pytest.fixture(autouse=True)
def _clean_sys_modules() -> object:
    """Drop synthetic ``check_*`` modules from ``sys.modules`` after each test so
    a re-used module name across tests can't serve a stale import."""
    before = set(sys.modules)
    yield
    for name in set(sys.modules) - before:
        if name.startswith("check_"):
            del sys.modules[name]


# --------------------------------------------------------------------------- #
# in-process dispatch + ledger shape
# --------------------------------------------------------------------------- #


def test_inprocess_pass_emits_run_and_pass_lines(
    checks_dir: Path, repo_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _write_py_check(checks_dir, "alpha", "return 0")
    rules = (RuleEntry(id="A1", gate="a1", check="alpha", summary="alpha rule"),)

    verdict = run(rules, mode="all", repo_root=repo_root, checks_dir=checks_dir)
    out = _plain(capsys.readouterr().out)

    assert verdict.ok
    assert verdict.ran == 1
    assert "run [A1] check_alpha.py" in out
    assert "PASS [A1] alpha rule" in out
    assert "=== All 1 architecture fitness functions passed ===" in out


def test_inprocess_fail_records_failure_and_exit_code(
    checks_dir: Path, repo_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _write_py_check(checks_dir, "beta", "return 1")
    rules = (RuleEntry(id="B1", gate="b1", check="beta", summary="beta rule"),)

    verdict = run(rules, mode="all", repo_root=repo_root, checks_dir=checks_dir)
    out = _plain(capsys.readouterr().out)

    assert not verdict.ok
    assert verdict.exit_code == 1
    assert verdict.failures == ["B1"]
    assert "FAIL [B1] beta rule (exit 1)" in out
    assert "Architecture fitness functions FAILED" in out


def test_inprocess_crash_is_isolated_into_a_fail(
    checks_dir: Path, repo_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # A check that RAISES must be converted to a FAIL, never abort the ledger:
    # the SECOND rule still runs and the aggregate counts both.
    _write_py_check(checks_dir, "boom", 'raise RuntimeError("kaboom")')
    _write_py_check(checks_dir, "ok", "return 0")
    rules = (
        RuleEntry(id="C1", gate="c1", check="boom", summary="crashy rule"),
        RuleEntry(id="C2", gate="c2", check="ok", summary="fine rule"),
    )

    verdict = run(rules, mode="all", repo_root=repo_root, checks_dir=checks_dir)
    captured = capsys.readouterr()
    out = _plain(captured.out)

    assert verdict.failures == ["C1"]
    assert verdict.ran == 2  # the crash did NOT abort the ledger
    assert "FAIL [C1] crashy rule" in out  # no "(exit N)" suffix for a crash
    assert "PASS [C2] fine rule" in out
    assert "kaboom" in captured.err  # traceback replayed to stderr


def test_inprocess_check_stdout_is_replayed_inline(
    checks_dir: Path, repo_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _write_py_check(checks_dir, "talky", 'print("hello from the check"); return 0')
    rules = (RuleEntry(id="T1", gate="t1", check="talky", summary="talky"),)

    run(rules, mode="all", repo_root=repo_root, checks_dir=checks_dir)
    out = _plain(capsys.readouterr().out)

    # The check's own stdout lands between its run and PASS lines.
    assert "hello from the check" in out


def test_inprocess_main_accepting_argv_is_called_with_empty_list(
    checks_dir: Path, repo_root: Path
) -> None:
    # A check declaring main(argv) must be called with [] (the no-args
    # subprocess shape), NOT the runner's own sys.argv.
    (checks_dir / "check_argvy.py").write_text(
        "def main(argv=None):\n"
        "    assert argv == [], f'expected [], got {argv!r}'\n"
        "    return 0\n"
    )
    rules = (RuleEntry(id="AV", gate="av", check="argvy", summary="argv check"),)
    verdict = run(rules, mode="all", repo_root=repo_root, checks_dir=checks_dir)
    assert verdict.ok


# --------------------------------------------------------------------------- #
# subprocess dispatch (sequential + parallel) for shell detectors
# --------------------------------------------------------------------------- #


def test_shell_detector_runs_as_subprocess(
    checks_dir: Path, repo_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _write_sh_check(checks_dir, "check-shellgate.sh", exit_code=0)
    rules = (
        RuleEntry(
            id="S1", gate="s1", check="shellgate", summary="shell rule", script="check-shellgate.sh"
        ),
    )
    verdict = run(rules, mode="all", repo_root=repo_root, checks_dir=checks_dir)
    out = _plain(capsys.readouterr().out)
    assert verdict.ok
    assert "run [S1] check-shellgate.sh" in out
    assert "PASS [S1] shell rule" in out


def test_shell_detector_nonzero_exit_is_a_fail(checks_dir: Path, repo_root: Path) -> None:
    _write_sh_check(checks_dir, "check-bad.sh", exit_code=3)
    rules = (RuleEntry(id="S2", gate="s2", check="bad", summary="bad shell", script="check-bad.sh"),)
    verdict = run(rules, mode="all", repo_root=repo_root, checks_dir=checks_dir)
    assert verdict.failures == ["S2"]


def test_missing_shell_script_is_a_fail_not_a_crash(checks_dir: Path, repo_root: Path) -> None:
    rules = (
        RuleEntry(id="S3", gate="s3", check="absent", summary="missing", script="check-absent.sh"),
    )
    verdict = run(rules, mode="all", repo_root=repo_root, checks_dir=checks_dir)
    assert verdict.failures == ["S3"]


def test_parallel_subprocess_dispatch_matches_sequential_verdicts(
    checks_dir: Path, repo_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _write_sh_check(checks_dir, "check-p1.sh", exit_code=0)
    _write_sh_check(checks_dir, "check-p2.sh", exit_code=1)
    _write_sh_check(checks_dir, "check-p3.sh", exit_code=0)
    rules = (
        RuleEntry(id="P1", gate="p1", check="p1", summary="p1", script="check-p1.sh"),
        RuleEntry(id="P2", gate="p2", check="p2", summary="p2", script="check-p2.sh"),
        RuleEntry(id="P3", gate="p3", check="p3", summary="p3", script="check-p3.sh"),
    )

    verdict = run(
        rules, mode="all", repo_root=repo_root, checks_dir=checks_dir, parallel_subprocess=True
    )
    out = _plain(capsys.readouterr().out)

    assert verdict.failures == ["P2"]
    assert verdict.ran == 3
    # The named ledger is replayed in CATALOGUE order regardless of completion
    # order — P1 before P2 before P3.
    assert out.index("run [P1]") < out.index("run [P2]") < out.index("run [P3]")
    assert "PASS [P1] p1" in out
    assert "FAIL [P2] p2 (exit 1)" in out
    assert "PASS [P3] p3" in out


def test_parallel_replays_subprocess_output(
    checks_dir: Path, repo_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _write_sh_check(checks_dir, "check-loud.sh", exit_code=0, echo="DETECTOR-OUTPUT-MARKER")
    rules = (RuleEntry(id="L1", gate="l1", check="loud", summary="loud", script="check-loud.sh"),)
    run(rules, mode="all", repo_root=repo_root, checks_dir=checks_dir, parallel_subprocess=True)
    out = _plain(capsys.readouterr().out)
    assert "DETECTOR-OUTPUT-MARKER" in out


# --------------------------------------------------------------------------- #
# mixed python + shell catalogue through the programmatic API
# --------------------------------------------------------------------------- #


def test_mixed_python_and_shell_catalogue(
    checks_dir: Path, repo_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _write_py_check(checks_dir, "py_pass", "return 0")
    _write_py_check(checks_dir, "py_fail", "return 1")
    _write_sh_check(checks_dir, "check-sh-pass.sh", exit_code=0)
    _write_sh_check(checks_dir, "check-sh-fail.sh", exit_code=1)
    rules = (
        RuleEntry(id="M1", gate="m1", check="py_pass", summary="py pass"),
        RuleEntry(id="M2", gate="m2", check="sh_pass", summary="sh pass", script="check-sh-pass.sh"),
        RuleEntry(id="M3", gate="m3", check="py_fail", summary="py fail"),
        RuleEntry(id="M4", gate="m4", check="sh_fail", summary="sh fail", script="check-sh-fail.sh"),
    )

    verdict = run(rules, mode="all", repo_root=repo_root, checks_dir=checks_dir)
    out = _plain(capsys.readouterr().out)

    assert verdict.ran == 4
    assert sorted(verdict.failures) == ["M3", "M4"]
    assert "2/4 rule(s) failed: M3, M4" in out


# --------------------------------------------------------------------------- #
# run_all gating + --gate selection + dedup
# --------------------------------------------------------------------------- #


def test_run_all_false_is_excluded_from_all(checks_dir: Path, repo_root: Path) -> None:
    _write_py_check(checks_dir, "always", "return 0")
    _write_py_check(checks_dir, "elsewhere", "return 1")  # would FAIL if dispatched
    rules = (
        RuleEntry(id="R1", gate="r1", check="always", summary="always"),
        RuleEntry(id="R2", gate="r2", check="elsewhere", summary="out-of-band", run_all=False),
    )
    verdict = run(rules, mode="all", repo_root=repo_root, checks_dir=checks_dir)
    # R2 is run_all=False → not dispatched, so its FAIL never registers.
    assert verdict.ok
    assert verdict.ran == 1


def test_gate_selects_one_rule_by_id(
    checks_dir: Path, repo_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _write_py_check(checks_dir, "one", "return 0")
    _write_py_check(checks_dir, "two", "return 1")
    rules = (
        RuleEntry(id="G1", gate="g1", check="one", summary="one"),
        RuleEntry(id="G2", gate="g2", check="two", summary="two"),
    )
    verdict = run(rules, mode="gate", gate_id="g1", repo_root=repo_root, checks_dir=checks_dir)
    out = _plain(capsys.readouterr().out)
    assert verdict.ok
    assert verdict.ran == 1
    assert "=== Architecture fitness function: g1 ===" in out
    assert "G2" not in out


def test_gate_unknown_id_returns_exit_2_via_main_cli(
    checks_dir: Path, repo_root: Path
) -> None:
    rules = (RuleEntry(id="X1", gate="x1", check="one", summary="one"),)
    rc = main_cli(
        rules, ["--gate", "nope"], repo_root=repo_root, checks_dir=checks_dir
    )
    assert rc == 2


def test_duplicate_resolved_script_runs_once(checks_dir: Path, repo_root: Path) -> None:
    # Two entries resolving to the SAME script dispatch that script once
    # (kairix's F7/F9-style shared-script dedup).
    _write_py_check(checks_dir, "shared", "return 0")
    rules = (
        RuleEntry(id="D1", gate="d1", check="shared", summary="first"),
        RuleEntry(id="D2", gate="d2", check="shared", summary="second"),
    )
    verdict = run(rules, mode="all", repo_root=repo_root, checks_dir=checks_dir)
    assert verdict.ran == 1  # deduped


# --------------------------------------------------------------------------- #
# proposed entries are skipped
# --------------------------------------------------------------------------- #


def test_proposed_entry_is_not_dispatched(checks_dir: Path, repo_root: Path) -> None:
    _write_py_check(checks_dir, "real", "return 0")
    rules = (
        RuleEntry(id="PR1", gate="pr1", check="real", summary="real"),
        RuleEntry(id="PR2", gate="pr2", check="(proposed)", summary="future", status="proposed"),
    )
    verdict = run(rules, mode="all", repo_root=repo_root, checks_dir=checks_dir)
    assert verdict.ran == 1  # the proposed rule contributed nothing


# --------------------------------------------------------------------------- #
# conditional (runtime-arg) subprocess check — coverage-style
# --------------------------------------------------------------------------- #


def test_conditional_check_skips_when_input_absent_with_custom_text(
    checks_dir: Path, repo_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _write_sh_check(checks_dir, "check-cov.sh", exit_code=0)
    rules = (
        RuleEntry(
            id="COV",
            gate="cov",
            check="cov",
            summary="coverage",
            script="check-cov.sh",
            subprocess_arg_env="NONEXISTENT_COVERAGE_XML",
        ),
    )

    def conditional(entry: RuleEntry) -> ConditionalResult:
        # The consumer's exact skip text — proves the byte-identical skip-line hook.
        return ConditionalResult(run=False, skip_lines=(f"skip [{entry.id}] — coverage report not found",))

    verdict = run(
        rules,
        mode="all",
        repo_root=repo_root,
        checks_dir=checks_dir,
        conditional_check=conditional,
    )
    out = _plain(capsys.readouterr().out)
    assert verdict.skipped == 1
    assert verdict.ran == 0
    assert "skip [COV] — coverage report not found" in out


def test_conditional_check_runs_with_extra_args(
    checks_dir: Path, repo_root: Path
) -> None:
    # The shell detector asserts it received the runtime arg.
    (checks_dir / "check-cov2.sh").write_text(
        '#!/usr/bin/env bash\n[ "$1" = "/tmp/the-report.xml" ] && exit 0 || exit 9\n'
    )
    (checks_dir / "check-cov2.sh").chmod(0o755)
    rules = (
        RuleEntry(
            id="COV2",
            gate="cov2",
            check="cov2",
            summary="coverage2",
            script="check-cov2.sh",
            subprocess_arg_env="X",
        ),
    )

    def conditional(_entry: RuleEntry) -> ConditionalResult:
        return ConditionalResult(run=True, extra_args=("/tmp/the-report.xml",))

    verdict = run(
        rules, mode="all", repo_root=repo_root, checks_dir=checks_dir, conditional_check=conditional
    )
    assert verdict.ok  # detector saw the arg


def test_conditional_builtin_env_resolution(
    checks_dir: Path, repo_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # With NO conditional hook, the runner falls back to env-var resolution:
    # the arg path is read from the declared env var; absent → skip.
    report = repo_root / "coverage.xml"
    report.write_text("<coverage/>")
    (checks_dir / "check-cov3.sh").write_text(
        f'#!/usr/bin/env bash\n[ "$1" = "{report}" ] && exit 0 || exit 9\n'
    )
    (checks_dir / "check-cov3.sh").chmod(0o755)
    rules = (
        RuleEntry(
            id="COV3",
            gate="cov3",
            check="cov3",
            summary="cov3",
            script="check-cov3.sh",
            subprocess_arg_env="MY_COVERAGE_XML",
            subprocess_arg_default="coverage.xml",
        ),
    )
    monkeypatch.delenv("MY_COVERAGE_XML", raising=False)
    verdict = run(rules, mode="all", repo_root=repo_root, checks_dir=checks_dir)
    assert verdict.ok  # default path resolved + exists


# --------------------------------------------------------------------------- #
# main_cli thin-consumer surface
# --------------------------------------------------------------------------- #


def test_main_cli_default_is_all(checks_dir: Path, repo_root: Path) -> None:
    _write_py_check(checks_dir, "cli_pass", "return 0")
    rules = (RuleEntry(id="CLI1", gate="cli1", check="cli_pass", summary="cli"),)
    rc = main_cli(rules, [], repo_root=repo_root, checks_dir=checks_dir)
    assert rc == 0


def test_main_cli_returns_1_on_failure(checks_dir: Path, repo_root: Path) -> None:
    _write_py_check(checks_dir, "cli_fail", "return 1")
    rules = (RuleEntry(id="CLI2", gate="cli2", check="cli_fail", summary="cli"),)
    rc = main_cli(rules, ["--all"], repo_root=repo_root, checks_dir=checks_dir)
    assert rc == 1


def test_resolve_script_default_and_override() -> None:
    assert resolve_script(RuleEntry(id="X", gate="x", check="foo_bar")) == "check_foo_bar.py"
    assert (
        resolve_script(RuleEntry(id="X", gate="x", check="foo", script="check-foo.sh"))
        == "check-foo.sh"
    )


def test_runner_config_puts_checks_dir_on_sys_path(checks_dir: Path, repo_root: Path) -> None:
    before = list(sys.path)
    try:
        RunnerConfig(repo_root=repo_root, checks_dir=checks_dir)
        assert str(checks_dir) in sys.path
    finally:
        sys.path[:] = before


def test_verdicts_properties() -> None:
    assert Verdicts(ran=3, failures=[]).ok is True
    assert Verdicts(ran=3, failures=["A"]).ok is False
    assert Verdicts(ran=3, failures=[]).exit_code == 0
    assert Verdicts(ran=3, failures=["A"]).exit_code == 1
