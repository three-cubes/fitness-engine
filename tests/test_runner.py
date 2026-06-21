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
    Colours,
    ConditionalResult,
    RunnerConfig,
    Verdicts,
    main_cli,
    make_env_path_conditional_check,
    print_aggregate,
    resolve_script,
    run,
    select_all,
    select_gate,
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


# --------------------------------------------------------------------------- #
# make_env_path_conditional_check — declarative ConditionalCheck factory (1.4)
#
# Generalises kairix's _make_conditional_check + _coverage_xml_path: resolve a
# runtime-arg path from an env var (else a repo-relative default), run with it
# appended when present, or skip with the consumer's EXACT skip lines when forced
# (--skip-coverage-style) or absent. The env-var name, default, force predicate,
# and both skip-line sets are all CONFIG.
# --------------------------------------------------------------------------- #


def test_conditional_factory_runs_with_resolved_default_path(tmp_path: Path) -> None:
    report = tmp_path / "coverage.xml"
    report.write_text("<coverage/>")
    hook = make_env_path_conditional_check(
        env_var="MY_COV_XML",
        default_rel="coverage.xml",
        repo_root=tmp_path,
    )
    entry = RuleEntry(id="F7", gate="f7", check="cov", subprocess_arg_env="MY_COV_XML")
    result = hook(entry)
    assert result is not None
    assert result.run is True
    assert result.extra_args == (str(report),)


def test_conditional_factory_env_var_wins_over_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env_report = tmp_path / "from-env.xml"
    env_report.write_text("<coverage/>")
    (tmp_path / "coverage.xml").write_text("<coverage/>")  # default also present
    monkeypatch.setenv("MY_COV_XML", str(env_report))
    hook = make_env_path_conditional_check(
        env_var="MY_COV_XML", default_rel="coverage.xml", repo_root=tmp_path
    )
    result = hook(RuleEntry(id="F7", gate="f7", check="cov", subprocess_arg_env="MY_COV_XML"))
    assert result is not None
    assert result.extra_args == (str(env_report),)


def test_conditional_factory_skips_when_path_absent_with_exact_lines(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("MY_COV_XML", raising=False)
    hook = make_env_path_conditional_check(
        env_var="MY_COV_XML",
        default_rel="coverage.xml",  # does not exist
        repo_root=tmp_path,
        absent_skip_lines=("skip [F7] check_cov.py — coverage report not found", "   run: pytest --cov first"),
    )
    result = hook(RuleEntry(id="F7", gate="f7", check="cov", subprocess_arg_env="MY_COV_XML"))
    assert result is not None
    assert result.run is False
    assert result.skip_lines == (
        "skip [F7] check_cov.py — coverage report not found",
        "   run: pytest --cov first",
    )


def test_conditional_factory_force_skip_short_circuits_with_exact_lines(tmp_path: Path) -> None:
    # The --skip-coverage path: force_skip() True ⇒ skip with force_skip_lines,
    # even if the report exists.
    (tmp_path / "coverage.xml").write_text("<coverage/>")
    hook = make_env_path_conditional_check(
        env_var="MY_COV_XML",
        default_rel="coverage.xml",
        repo_root=tmp_path,
        force_skip=lambda: True,
        force_skip_lines=("skip [F7] check_cov.py — --skip-coverage",),
    )
    result = hook(RuleEntry(id="F7", gate="f7", check="cov", subprocess_arg_env="MY_COV_XML"))
    assert result is not None
    assert result.run is False
    assert result.skip_lines == ("skip [F7] check_cov.py — --skip-coverage",)


def test_conditional_factory_force_skip_false_falls_through_to_run(tmp_path: Path) -> None:
    (tmp_path / "coverage.xml").write_text("<coverage/>")
    hook = make_env_path_conditional_check(
        env_var="MY_COV_XML",
        default_rel="coverage.xml",
        repo_root=tmp_path,
        force_skip=lambda: False,
        force_skip_lines=("unused",),
    )
    result = hook(RuleEntry(id="F7", gate="f7", check="cov", subprocess_arg_env="MY_COV_XML"))
    assert result is not None
    assert result.run is True


def test_conditional_factory_wires_into_runner_skip(
    checks_dir: Path, repo_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # End-to-end: the factory hook passed as conditional_check skips the rule and
    # prints the consumer's exact lines.
    _write_sh_check(checks_dir, "check-cov.sh", exit_code=0)
    rules = (
        RuleEntry(
            id="COVE", gate="cove", check="cov", summary="coverage",
            script="check-cov.sh", subprocess_arg_env="MY_COV_XML",
        ),
    )
    hook = make_env_path_conditional_check(
        env_var="MY_COV_XML",
        default_rel="nope.xml",
        repo_root=repo_root,
        absent_skip_lines=("skip [COVE] check-cov.sh — coverage report not found",),
    )
    verdict = run(rules, mode="all", repo_root=repo_root, checks_dir=checks_dir, conditional_check=hook)
    out = _plain(capsys.readouterr().out)
    assert verdict.skipped == 1
    assert "skip [COVE] check-cov.sh — coverage report not found" in out


def test_conditional_factory_per_entry_skip_line_differs_by_id(tmp_path: Path) -> None:
    # DEFECT-1 regression: F7 and F9 share ONE script (check_per_file_coverage.py)
    # and differ ONLY by entry.id. A static skip tuple emits IDENTICAL text for
    # both; the per-entry callable interpolates `skip [{entry.id}]` so the two
    # produced lines differ — the byte-identity ledger contract for shared-script
    # rules. The fn form must win, receive the RuleEntry, and resolve per id.
    hook = make_env_path_conditional_check(
        env_var="MY_COV_XML",
        default_rel="nope.xml",  # absent → exercise the absent-skip path
        repo_root=tmp_path,
        absent_skip_line_fn=lambda e: (
            f"skip [{e.id}] check_per_file_coverage.py — coverage report not found",
            f"   run: pytest --cov first ({e.id})",
        ),
    )
    f7 = RuleEntry(
        id="F7", gate="f7", check="per_file_coverage",
        script="check_per_file_coverage.py", subprocess_arg_env="MY_COV_XML",
    )
    f9 = RuleEntry(
        id="F9", gate="f9", check="per_file_coverage",
        script="check_per_file_coverage.py", subprocess_arg_env="MY_COV_XML",
    )
    r7 = hook(f7)
    r9 = hook(f9)
    assert r7 is not None and r9 is not None
    assert r7.run is False and r9.run is False
    assert r7.skip_lines == (
        "skip [F7] check_per_file_coverage.py — coverage report not found",
        "   run: pytest --cov first (F7)",
    )
    assert r9.skip_lines == (
        "skip [F9] check_per_file_coverage.py — coverage report not found",
        "   run: pytest --cov first (F9)",
    )
    # The two skip ledgers are DISTINCT despite sharing one script.
    assert r7.skip_lines != r9.skip_lines


def test_conditional_factory_force_skip_line_fn_receives_entry(tmp_path: Path) -> None:
    # DEFECT-1: the forced-skip path also accepts a per-entry callable that wins
    # over the static tuple and interpolates the id.
    (tmp_path / "coverage.xml").write_text("<coverage/>")  # present, but forced
    hook = make_env_path_conditional_check(
        env_var="MY_COV_XML",
        default_rel="coverage.xml",
        repo_root=tmp_path,
        force_skip=lambda: True,
        force_skip_line_fn=lambda e: (f"skip [{e.id}] check_per_file_coverage.py — --skip-coverage",),
    )
    r7 = hook(RuleEntry(id="F7", gate="f7", check="cov", subprocess_arg_env="MY_COV_XML"))
    r9 = hook(RuleEntry(id="F9", gate="f9", check="cov", subprocess_arg_env="MY_COV_XML"))
    assert r7 is not None and r9 is not None
    assert r7.skip_lines == ("skip [F7] check_per_file_coverage.py — --skip-coverage",)
    assert r9.skip_lines == ("skip [F9] check_per_file_coverage.py — --skip-coverage",)


def test_conditional_factory_fn_wins_over_static_tuple(tmp_path: Path) -> None:
    # DEFECT-1: precedence — when both the static tuple and the fn are supplied,
    # the fn wins (per-entry interpolation supersedes the fixed text).
    hook = make_env_path_conditional_check(
        env_var="MY_COV_XML",
        default_rel="nope.xml",
        repo_root=tmp_path,
        absent_skip_lines=("static — WRONG",),
        absent_skip_line_fn=lambda e: (f"skip [{e.id}] — dynamic wins",),
    )
    r = hook(RuleEntry(id="F7", gate="f7", check="cov", subprocess_arg_env="MY_COV_XML"))
    assert r is not None
    assert r.skip_lines == ("skip [F7] — dynamic wins",)


# --------------------------------------------------------------------------- #
# main_cli extra_flags + post_parse — consumer-specific flags (Task 1.4)
#
# Retires kairix's forked main()/--skip-coverage: a consumer declares its flag
# via extra_flags and maps the parsed Namespace to extra run() kwargs (e.g. a
# conditional_check built from the flag) via post_parse.
# --------------------------------------------------------------------------- #


def test_main_cli_extra_flag_is_parsed_and_threaded_via_post_parse(
    checks_dir: Path, repo_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # The coverage rule would FAIL if dispatched; --skip-coverage must skip it.
    _write_sh_check(checks_dir, "check-covx.sh", exit_code=1)
    rules = (
        RuleEntry(
            id="CX", gate="cx", check="covx", summary="cov", script="check-covx.sh",
            subprocess_arg_env="MY_COV_XML",
        ),
    )

    seen: dict[str, object] = {}

    def post_parse(ns: object) -> dict[str, object]:
        seen["skip_coverage"] = ns.skip_coverage  # type: ignore[attr-defined]
        hook = make_env_path_conditional_check(
            env_var="MY_COV_XML",
            default_rel="coverage.xml",
            repo_root=repo_root,
            force_skip=lambda: ns.skip_coverage,  # type: ignore[attr-defined]
            force_skip_lines=("skip [CX] check-covx.sh — --skip-coverage",),
        )
        return {"conditional_check": hook}

    rc = main_cli(
        rules,
        ["--all", "--skip-coverage"],
        repo_root=repo_root,
        checks_dir=checks_dir,
        extra_flags=[("--skip-coverage", {"action": "store_true"})],
        post_parse=post_parse,
    )
    out = _plain(capsys.readouterr().out)
    assert seen["skip_coverage"] is True
    assert rc == 0  # the failing rule was skipped
    assert "skip [CX] check-covx.sh — --skip-coverage" in out


def test_main_cli_extra_flag_absent_defaults_and_dispatches(
    checks_dir: Path, repo_root: Path
) -> None:
    # Without --skip-coverage the post_parse hook lets the rule run (and here the
    # report is absent → the factory skips on absence, not on force).
    _write_sh_check(checks_dir, "check-covy.sh", exit_code=0)
    rules = (
        RuleEntry(
            id="CY", gate="cy", check="covy", summary="cov", script="check-covy.sh",
            subprocess_arg_env="MY_COV_XML",
        ),
    )

    def post_parse(ns: object) -> dict[str, object]:
        hook = make_env_path_conditional_check(
            env_var="MY_COV_XML",
            default_rel="nope.xml",
            repo_root=repo_root,
            force_skip=lambda: ns.skip_coverage,  # type: ignore[attr-defined]
            force_skip_lines=("forced",),
            absent_skip_lines=("absent",),
        )
        return {"conditional_check": hook}

    rc = main_cli(
        rules,
        ["--all"],
        repo_root=repo_root,
        checks_dir=checks_dir,
        extra_flags=[("--skip-coverage", {"action": "store_true"})],
        post_parse=post_parse,
    )
    assert rc == 0  # skipped on absence (not forced); no failure registered


def test_main_cli_without_extra_flags_is_byte_identical(
    checks_dir: Path, repo_root: Path
) -> None:
    # The default (no extra_flags / post_parse) is unchanged from v0.3.0.
    _write_py_check(checks_dir, "plain", "return 0")
    rules = (RuleEntry(id="PL", gate="pl", check="plain", summary="plain"),)
    assert main_cli(rules, ["--all"], repo_root=repo_root, checks_dir=checks_dir) == 0


# --------------------------------------------------------------------------- #
# RuleEntry argv-exception fields (Task 1.5)
#
# Generalises taz's _SCRIPT_PATH_OVERRIDES (hermetic smoke), _STATIC_EXTRA_ARGS
# (mutation ratchet --allow-missing-current), _orphan_files_extra
# (ORPHAN_FILES_STRICT → --strict). All declarative on the RuleEntry now.
# --------------------------------------------------------------------------- #


def test_script_path_override_resolves_outside_checks_dir(
    tmp_path: Path, repo_root: Path
) -> None:
    # The override path is resolved relative to the REPO ROOT, not the checks
    # dir — taz's hermetic smoke lives at tests/smoke/hermetic.sh.
    checks_dir = repo_root / "scripts" / "checks"
    checks_dir.mkdir(parents=True)
    smoke_dir = repo_root / "tests" / "smoke"
    smoke_dir.mkdir(parents=True)
    (smoke_dir / "hermetic.sh").write_text("#!/usr/bin/env bash\nexit 0\n")
    (smoke_dir / "hermetic.sh").chmod(0o755)
    rules = (
        RuleEntry(
            id="HSMOKE", gate="hsmoke", check="hermetic", summary="hermetic smoke",
            script="hermetic.sh", script_path_override="tests/smoke/hermetic.sh",
        ),
    )
    verdict = run(rules, mode="all", repo_root=repo_root, checks_dir=checks_dir)
    assert verdict.ok
    assert verdict.ran == 1


def test_static_extra_args_always_appended(repo_root: Path) -> None:
    checks_dir = repo_root / "scripts" / "checks"
    checks_dir.mkdir(parents=True)
    # The detector passes only if it sees the static arg.
    (checks_dir / "check-mut.sh").write_text(
        '#!/usr/bin/env bash\n[ "$1" = "--allow-missing-current" ] && exit 0 || exit 7\n'
    )
    (checks_dir / "check-mut.sh").chmod(0o755)
    rules = (
        RuleEntry(
            id="MUT", gate="mut", check="mut", summary="mutation ratchet",
            script="check-mut.sh", static_extra_args=("--allow-missing-current",),
        ),
    )
    verdict = run(rules, mode="all", repo_root=repo_root, checks_dir=checks_dir)
    assert verdict.ok


def test_env_gated_extra_arg_present_only_when_env_set(
    repo_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    checks_dir = repo_root / "scripts" / "checks"
    checks_dir.mkdir(parents=True)
    # Detector exits 0 IFF it sees --strict as $1.
    (checks_dir / "check-orphan.sh").write_text(
        '#!/usr/bin/env bash\n[ "$1" = "--strict" ] && exit 0 || exit 5\n'
    )
    (checks_dir / "check-orphan.sh").chmod(0o755)
    rules = (
        RuleEntry(
            id="ORPH", gate="orph", check="orphan", summary="orphan files",
            script="check-orphan.sh", env_gated_extra_args=(("ORPHAN_FILES_STRICT", "--strict"),),
        ),
    )
    # Env set → the gated arg appears → detector passes.
    monkeypatch.setenv("ORPHAN_FILES_STRICT", "1")
    assert run(rules, mode="all", repo_root=repo_root, checks_dir=checks_dir).ok

    # Env unset → the gated arg is absent → detector FAILs (proves gating).
    monkeypatch.delenv("ORPHAN_FILES_STRICT", raising=False)
    assert not run(rules, mode="all", repo_root=repo_root, checks_dir=checks_dir).ok


def test_static_and_env_gated_args_order(
    repo_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # static args come before env-gated args, both after any conditional arg.
    checks_dir = repo_root / "scripts" / "checks"
    checks_dir.mkdir(parents=True)
    (checks_dir / "check-both.sh").write_text(
        '#!/usr/bin/env bash\n[ "$1" = "--static" ] && [ "$2" = "--gated" ] && exit 0 || exit 4\n'
    )
    (checks_dir / "check-both.sh").chmod(0o755)
    rules = (
        RuleEntry(
            id="BOTH", gate="both", check="both", summary="both",
            script="check-both.sh",
            static_extra_args=("--static",),
            env_gated_extra_args=(("GATE_ENV", "--gated"),),
        ),
    )
    monkeypatch.setenv("GATE_ENV", "1")
    assert run(rules, mode="all", repo_root=repo_root, checks_dir=checks_dir).ok


# --------------------------------------------------------------------------- #
# public subprocess-dispatch mode + promoted ledger primitives (Task 1.6)
#
# So taz drops its 7 private-symbol imports and reimplemented dispatch.
# --------------------------------------------------------------------------- #


def test_dispatch_subprocess_routes_python_checks_through_subprocess(
    checks_dir: Path, repo_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # A pure-python check that would run IN-PROCESS by default is routed through
    # the guarded subprocess path when dispatch="subprocess". It writes a marker
    # to a file from its OWN process so we can prove it ran out-of-process.
    marker = repo_root / "ran_in_subprocess.txt"
    (checks_dir / "check_subp.py").write_text(
        "import os, sys\n"
        "def main():\n"
        f"    open(r'{marker}', 'w').write(str(os.getpid()))\n"
        "    return 0\n"
        "if __name__ == '__main__':\n"
        "    sys.exit(main())\n"
    )
    rules = (RuleEntry(id="SUBP", gate="subp", check="subp", summary="subp"),)
    verdict = run(
        rules, mode="all", repo_root=repo_root, checks_dir=checks_dir, dispatch="subprocess"
    )
    out = _plain(capsys.readouterr().out)
    assert verdict.ok
    assert verdict.ran == 1
    assert marker.exists()  # ran as a real child process
    child_pid = int(marker.read_text())
    import os as _os

    assert child_pid != _os.getpid()  # genuinely a different process
    assert "PASS [SUBP] subp" in out


def test_dispatch_subprocess_produces_same_aggregate_banner(
    checks_dir: Path, repo_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # Run-as-a-file needs a main guard (subprocess invokes the script directly).
    (checks_dir / "check_sp_ok.py").write_text(
        "import sys\ndef main():\n    return 0\nif __name__ == '__main__':\n    sys.exit(main())\n"
    )
    (checks_dir / "check_sp_bad.py").write_text(
        "import sys\ndef main():\n    return 1\nif __name__ == '__main__':\n    sys.exit(main())\n"
    )
    rules = (
        RuleEntry(id="SO", gate="so", check="sp_ok", summary="ok"),
        RuleEntry(id="SB", gate="sb", check="sp_bad", summary="bad"),
    )
    verdict = run(
        rules, mode="all", repo_root=repo_root, checks_dir=checks_dir, dispatch="subprocess"
    )
    out = _plain(capsys.readouterr().out)
    assert verdict.failures == ["SB"]
    assert "1/2 rule(s) failed: SB" in out


def test_main_cli_dispatch_subprocess_kwarg(checks_dir: Path, repo_root: Path) -> None:
    _write_py_check(checks_dir, "cli_subp", "import sys; sys.exit(0)")
    rules = (RuleEntry(id="CS", gate="cs", check="cli_subp", summary="cs"),)
    rc = main_cli(rules, ["--all"], repo_root=repo_root, checks_dir=checks_dir, dispatch="subprocess")
    assert rc == 0


def test_default_dispatch_is_inprocess(checks_dir: Path, repo_root: Path) -> None:
    # The v0.3.0 default: pure-python checks run in-process (no dispatch kwarg).
    marker = repo_root / "should_not_exist.txt"
    (checks_dir / "check_ip.py").write_text(
        "import os\n"
        "def main():\n"
        "    return 0\n"
    )
    rules = (RuleEntry(id="IP", gate="ip", check="ip", summary="ip"),)
    verdict = run(rules, mode="all", repo_root=repo_root, checks_dir=checks_dir)
    assert verdict.ok
    assert not marker.exists()


# promoted ledger primitives -------------------------------------------------- #


def test_select_all_is_public_and_filters_run_all_and_proposed() -> None:
    rules = (
        RuleEntry(id="A", gate="a", check="a"),
        RuleEntry(id="B", gate="b", check="b", run_all=False),
        RuleEntry(id="C", gate="c", check="(proposed)", status="proposed"),
    )
    assert [e.id for e in select_all(rules)] == ["A"]


def test_select_gate_is_public_and_case_insensitive() -> None:
    rules = (RuleEntry(id="F26", gate="f26", check="x"),)
    assert [e.id for e in select_gate(rules, "f26")] == ["F26"]
    assert select_gate(rules, "nope") == []


def test_print_aggregate_is_public(capsys: pytest.CaptureFixture[str]) -> None:
    print_aggregate(Verdicts(ran=2, failures=[]))
    assert "All 2 architecture fitness functions passed" in _plain(capsys.readouterr().out)
    print_aggregate(Verdicts(ran=2, failures=["X"]))
    assert "1/2 rule(s) failed: X" in _plain(capsys.readouterr().out)


def test_colours_namespace_is_public() -> None:
    # The colours taz imports as private _GREEN/_RED/_RESET/_YELLOW are exposed
    # as a public namespace.
    assert Colours.GREEN == "\033[0;32m"
    assert Colours.RED == "\033[0;31m"
    assert Colours.YELLOW == "\033[0;33m"
    assert Colours.RESET == "\033[0m"


def test_underscore_aliases_still_re_exported() -> None:
    # Back-compat: taz's private imports keep resolving until it migrates.
    from tc_fitness.runner import (
        _GREEN,
        _RED,
        _RESET,
        _YELLOW,
        _print_aggregate,
        _select_all,
        _select_gate,
    )

    assert _print_aggregate is print_aggregate
    assert _select_all is select_all
    assert _select_gate is select_gate
    assert (_GREEN, _RED, _YELLOW, _RESET) == (
        Colours.GREEN,
        Colours.RED,
        Colours.YELLOW,
        Colours.RESET,
    )


def test_argv_exception_fields_work_in_parallel_dispatch(
    repo_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The same argv assembly must hold on the parallel subprocess path.
    checks_dir = repo_root / "scripts" / "checks"
    checks_dir.mkdir(parents=True)
    (checks_dir / "check-par.sh").write_text(
        '#!/usr/bin/env bash\n[ "$1" = "--s" ] && exit 0 || exit 6\n'
    )
    (checks_dir / "check-par.sh").chmod(0o755)
    rules = (
        RuleEntry(
            id="PAR", gate="par", check="par", summary="par",
            script="check-par.sh", static_extra_args=("--s",),
        ),
    )
    assert run(
        rules, mode="all", repo_root=repo_root, checks_dir=checks_dir, parallel_subprocess=True
    ).ok


# --------------------------------------------------------------------------- #
# core: entries — config injection + in-process dispatch (v0.6.1)
# --------------------------------------------------------------------------- #

_CORE_DUP_FIXTURE = '''"""docstring."""


def a() -> None:
    raise ValueError("a repeated long literal")


def b() -> None:
    raise ValueError("a repeated long literal")


def c() -> None:
    raise ValueError("a repeated long literal")
'''


def _core_rule() -> tuple[RuleEntry, ...]:
    return (
        RuleEntry(
            id="no-duplicate-string",
            gate="no-duplicate-string",
            check="core:no_duplicate_string",
            summary="no duplicated literal",
        ),
    )


def test_core_entry_injects_config_and_flags(repo_root: Path, capsys: pytest.CaptureFixture[str]) -> None:
    (repo_root / "src").mkdir()
    (repo_root / "src" / "dup.py").write_text(_CORE_DUP_FIXTURE, encoding="utf-8")
    verdict = run(
        _core_rule(),
        mode="all",
        repo_root=repo_root,
        core_check_configs={"no_duplicate_string": {"roots": ["src"], "min_occurrences": 3}},
    )
    out = _plain(capsys.readouterr().out)
    assert not verdict.ok
    assert "FAIL [no-duplicate-string]" in out
    assert "dup.py" in out


def test_core_entry_without_config_is_vacuous(repo_root: Path) -> None:
    (repo_root / "src").mkdir()
    (repo_root / "src" / "dup.py").write_text(_CORE_DUP_FIXTURE, encoding="utf-8")
    # No config block → roots=() → nothing enumerated → vacuous pass.
    assert run(_core_rule(), mode="all", repo_root=repo_root).ok


def test_core_entry_in_process_even_under_subprocess_dispatch(
    repo_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (repo_root / "src").mkdir()
    (repo_root / "src" / "dup.py").write_text(_CORE_DUP_FIXTURE, encoding="utf-8")
    verdict = run(
        _core_rule(),
        mode="all",
        repo_root=repo_root,
        dispatch="subprocess",
        core_check_configs={"no_duplicate_string": {"roots": ["src"]}},
    )
    out = _plain(capsys.readouterr().out)
    assert not verdict.ok
    assert "FAIL [no-duplicate-string]" in out
    assert "check script not found" not in out


def test_core_entry_establish_baseline_then_passes(repo_root: Path) -> None:
    (repo_root / "src").mkdir()
    (repo_root / "src" / "dup.py").write_text(_CORE_DUP_FIXTURE, encoding="utf-8")
    cfg_kwargs = {
        "repo_root": repo_root,
        "core_check_configs": {"no_duplicate_string": {"roots": ["src"]}},
    }
    # Establish writes the baseline and passes…
    assert run(_core_rule(), mode="all", establish_baseline=True, **cfg_kwargs).ok  # type: ignore[arg-type]
    baseline = repo_root / ".architecture" / "baseline" / "no-duplicate-string-files.txt"
    assert baseline.exists()
    assert "src/dup.py" in baseline.read_text(encoding="utf-8")
    # …and the subsequent gate run passes (offender grandfathered).
    assert run(_core_rule(), mode="all", **cfg_kwargs).ok  # type: ignore[arg-type]
