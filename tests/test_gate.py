"""End-to-end tests for the ``tc-fitness run`` gate orchestrator.

Each test builds a synthetic repo (a tmp dir with a ``[tool.tc_fitness]`` config
and the step scripts/catalogue it references) and drives ``run_gate`` / ``main``,
so the orchestrator is exercised exactly as a consumer's CI / ``make check``
would invoke it — without depending on any real repo.

The proofs:

- a ``run`` (argv) step passes / fails on the child's exit code;
- a ``shell`` step runs through the shell (pipelines / ``$(...)`` work);
- step order is config order; a FAIL prints its agent-actionable fix:/next:;
- ``continue_on_error`` records a FAIL but does NOT gate the aggregate;
- ``allow_missing`` skips a missing program; without it a missing program FAILs;
- a ``catalogue`` step dispatches the consumer's RuleEntry catalogue through the
  shared runner, and ``--gate ID`` targets one rule;
- ``--only ID`` restricts to a subset; ``fail_fast`` stops at the first failure;
- the aggregate exit is 0 iff no gating step failed; a missing config exits 2.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

from tc_fitness.gate import main, run_gate
from tc_fitness.gate_config import load_config

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


def _plain(text: str) -> str:
    return _ANSI_RE.sub("", text)


def _write_config(repo: Path, body: str) -> None:
    # Write the gate config to a `.tc-fitness.toml` dedicated file, whose whole
    # document IS the config — so a top-level `[[steps]]` array nests correctly
    # without a `[tool.tc_fitness]` wrapper. (One test below covers the
    # pyproject `[tool.tc_fitness]` path explicitly; the rest use the dedicated
    # file purely to keep the TOML readable.)
    (repo / ".tc-fitness.toml").write_text(body)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    return tmp_path


@pytest.fixture(autouse=True)
def _clean_sys_modules() -> object:
    before = set(sys.modules)
    yield
    for name in set(sys.modules) - before:
        if name.startswith(("check_", "_synthetic_cat", "synthetic_cat")):
            del sys.modules[name]


# --------------------------------------------------------------------------- #
# run (argv) steps
# --------------------------------------------------------------------------- #


def test_run_step_passes_on_zero_exit(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _write_config(repo, '[[steps]]\nid = "ok"\nsummary = "true step"\nrun = ["true"]\n')
    outcome = run_gate(load_config(repo), repo)
    out = _plain(capsys.readouterr().out)
    assert outcome.ok
    assert outcome.exit_code == 0
    assert "run [ok] true step" in out
    assert "PASS [ok] true step" in out
    assert ": PASS ===" in out


def test_run_step_fails_on_nonzero_exit_and_prints_fix_next(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _write_config(
        repo,
        '[[steps]]\nid = "bad"\nrun = ["false"]\nfix = "stop returning 1"\nnext = "re-run tc-fitness run"\n',
    )
    outcome = run_gate(load_config(repo), repo)
    out = _plain(capsys.readouterr().out)
    assert not outcome.ok
    assert outcome.exit_code == 1
    assert outcome.gating_failures == ["bad"]
    assert "FAIL [bad]" in out
    assert "fix: stop returning 1" in out
    assert "next: re-run tc-fitness run" in out


def test_step_env_is_passed_to_child(repo: Path) -> None:
    # The child exits 0 IFF it sees the step-declared env var.
    _write_config(
        repo,
        '[[steps]]\nid = "env"\nshell = "[ \\"$GATE_MARK\\" = \\"yes\\" ]"\nenv = { GATE_MARK = "yes" }\n',
    )
    assert run_gate(load_config(repo), repo).ok


def test_step_cwd_is_relative_to_repo_root(repo: Path) -> None:
    sub = repo / "subdir"
    sub.mkdir()
    (sub / "marker.txt").write_text("x")
    _write_config(repo, '[[steps]]\nid = "cwd"\ncwd = "subdir"\nshell = "test -f marker.txt"\n')
    assert run_gate(load_config(repo), repo).ok


# --------------------------------------------------------------------------- #
# shell steps
# --------------------------------------------------------------------------- #


def test_shell_step_runs_through_shell(repo: Path) -> None:
    # A pipeline only works through the shell.
    _write_config(repo, '[[steps]]\nid = "pipe"\nshell = "echo hi | grep -q hi"\n')
    assert run_gate(load_config(repo), repo).ok


# --------------------------------------------------------------------------- #
# missing program
# --------------------------------------------------------------------------- #


def test_missing_program_fails_by_default(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _write_config(repo, '[[steps]]\nid = "gone"\nrun = ["definitely-not-a-real-prog-xyz"]\n')
    outcome = run_gate(load_config(repo), repo)
    out = _plain(capsys.readouterr().out)
    assert not outcome.ok
    assert "not on PATH" in out


def test_missing_program_skips_when_allow_missing(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _write_config(
        repo,
        '[[steps]]\nid = "opt"\nrun = ["definitely-not-a-real-prog-xyz"]\nallow_missing = true\n',
    )
    outcome = run_gate(load_config(repo), repo)
    out = _plain(capsys.readouterr().out)
    assert outcome.ok  # a skip never gates
    assert outcome.skipped == 1
    assert "SKIP [opt]" in out


# --------------------------------------------------------------------------- #
# continue_on_error
# --------------------------------------------------------------------------- #


def test_continue_on_error_fail_does_not_gate(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _write_config(
        repo,
        '[[steps]]\nid = "soft"\nrun = ["false"]\ncontinue_on_error = true\n'
        '[[steps]]\nid = "hard"\nrun = ["true"]\n',
    )
    outcome = run_gate(load_config(repo), repo)
    out = _plain(capsys.readouterr().out)
    # The soft step FAILs but is non-gating, so the aggregate is PASS.
    assert outcome.ok
    assert outcome.gating_failures == []
    assert "FAIL [soft]" in out
    assert ": PASS ===" in out


# --------------------------------------------------------------------------- #
# ordering, only, fail_fast
# --------------------------------------------------------------------------- #


def test_steps_run_in_config_order(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _write_config(
        repo,
        '[[steps]]\nid = "first"\nrun = ["true"]\n'
        '[[steps]]\nid = "second"\nrun = ["true"]\n'
        '[[steps]]\nid = "third"\nrun = ["true"]\n',
    )
    run_gate(load_config(repo), repo)
    out = _plain(capsys.readouterr().out)
    assert out.index("run [first]") < out.index("run [second]") < out.index("run [third]")


def test_only_restricts_to_named_steps(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _write_config(
        repo,
        '[[steps]]\nid = "a"\nrun = ["true"]\n[[steps]]\nid = "b"\nrun = ["false"]\n',  # would fail if run
    )
    outcome = run_gate(load_config(repo), repo, only=["a"])
    out = _plain(capsys.readouterr().out)
    assert outcome.ok  # b was not run
    assert "run [a]" in out
    assert "run [b]" not in out


def test_fail_fast_stops_at_first_gating_failure(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _write_config(
        repo,
        'fail_fast = true\n[[steps]]\nid = "x"\nrun = ["false"]\n[[steps]]\nid = "y"\nrun = ["true"]\n',
    )
    outcome = run_gate(load_config(repo), repo)
    out = _plain(capsys.readouterr().out)
    assert not outcome.ok
    assert "run [y]" not in out  # never reached
    assert "fail_fast" in out


# --------------------------------------------------------------------------- #
# catalogue step — dispatch the consumer's RuleEntry catalogue via the runner
# --------------------------------------------------------------------------- #


def _write_synthetic_catalogue(repo: Path) -> None:
    """A repo whose catalogue lives at scripts/checks/synthetic_cat.py with two
    check modules (one pass, one fail) — exercises the in-process dispatch."""
    checks = repo / "scripts" / "checks"
    checks.mkdir(parents=True)
    (checks / "__init__.py").write_text("")
    (repo / "scripts" / "__init__.py").write_text("")
    (checks / "check_alpha.py").write_text("def main():\n    return 0\n")
    (checks / "check_beta.py").write_text("def main():\n    return 0\n")
    (checks / "synthetic_cat.py").write_text(
        "from tc_fitness.catalogue import RuleEntry\n"
        "ALL_ENTRIES = (\n"
        "    RuleEntry(id='A1', gate='a1', check='alpha', summary='alpha'),\n"
        "    RuleEntry(id='B1', gate='b1', check='beta', summary='beta'),\n"
        ")\n"
    )


def test_catalogue_step_dispatches_rules(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _write_synthetic_catalogue(repo)
    _write_config(
        repo,
        "[[steps]]\n"
        'id = "fitness"\n'
        'summary = "architecture fitness functions"\n'
        'catalogue = "scripts.checks.synthetic_cat:ALL_ENTRIES"\n'
        'checks_dir = "scripts/checks"\n',
    )
    outcome = run_gate(load_config(repo), repo)
    out = _plain(capsys.readouterr().out)
    assert outcome.ok
    # The runner's own per-rule ledger prints inline under the step.
    assert "PASS [A1] alpha" in out
    assert "PASS [B1] beta" in out
    assert "PASS [fitness]" in out


def test_catalogue_step_fails_when_a_rule_fails(repo: Path) -> None:
    checks = repo / "scripts" / "checks"
    checks.mkdir(parents=True)
    (checks / "__init__.py").write_text("")
    (repo / "scripts" / "__init__.py").write_text("")
    (checks / "check_bad.py").write_text("def main():\n    return 1\n")
    (checks / "cat2.py").write_text(
        "from tc_fitness.catalogue import RuleEntry\n"
        "ALL_ENTRIES = (RuleEntry(id='X1', gate='x1', check='bad', summary='bad'),)\n"
    )
    _write_config(
        repo,
        '[[steps]]\nid = "f"\ncatalogue = "scripts.checks.cat2:ALL_ENTRIES"\nchecks_dir = "scripts/checks"\n',
    )
    assert not run_gate(load_config(repo), repo).ok


def test_catalogue_step_gate_id_targets_one_rule(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _write_synthetic_catalogue(repo)
    _write_config(
        repo,
        '[[steps]]\nid = "f"\ncatalogue = "scripts.checks.synthetic_cat:ALL_ENTRIES"\n'
        'checks_dir = "scripts/checks"\n',
    )
    run_gate(load_config(repo), repo, gate_id="A1")
    out = _plain(capsys.readouterr().out)
    assert "run [A1]" in out
    assert "run [B1]" not in out  # only A1 targeted


def test_catalogue_step_unresolvable_ref_is_a_fail(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _write_config(
        repo,
        '[[steps]]\nid = "f"\ncatalogue = "nope.module:ALL_ENTRIES"\n',
    )
    outcome = run_gate(load_config(repo), repo)
    out = _plain(capsys.readouterr().out)
    assert not outcome.ok
    assert "could not load catalogue" in out


# --------------------------------------------------------------------------- #
# --staged smoke tier — the <60s fast-feedback entrypoint
# --------------------------------------------------------------------------- #


def test_staged_catalogue_step_uses_staged_selection(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    # With --staged and NO staged paths, the runner's staged selection runs
    # every rule (fail-safe), printing its staged banner — proving the catalogue
    # was dispatched in --staged mode, not --all.
    _write_synthetic_catalogue(repo)
    _write_config(
        repo,
        '[[steps]]\nid = "fitness"\ncatalogue = "scripts.checks.synthetic_cat:ALL_ENTRIES"\n'
        'checks_dir = "scripts/checks"\n',
    )
    outcome = run_gate(load_config(repo), repo, staged=True)
    out = _plain(capsys.readouterr().out)
    assert outcome.ok
    assert "staged smoke" in out  # the smoke banner
    assert "staged selection:" in out  # the runner's --staged ledger footer


def test_staged_skips_steps_flagged_skip_when_staged(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    # An expensive full-tree leg flagged skip_when_staged is dropped from the
    # smoke with a transparent SKIP; the cheap leg still runs.
    _write_config(
        repo,
        '[[steps]]\nid = "cheap"\nrun = ["true"]\n'
        '[[steps]]\nid = "expensive"\nrun = ["false"]\nskip_when_staged = true\n',
    )
    outcome = run_gate(load_config(repo), repo, staged=True)
    out = _plain(capsys.readouterr().out)
    # The failing expensive leg was skipped, so the smoke is green.
    assert outcome.ok
    assert "SKIP [expensive]" in out
    assert "skip_when_staged" in out
    assert "PASS [cheap]" in out


def test_non_staged_run_still_runs_skip_when_staged_steps(repo: Path) -> None:
    # skip_when_staged ONLY affects --staged; a normal full run still executes
    # the flagged step (so it gates as before).
    _write_config(
        repo,
        '[[steps]]\nid = "expensive"\nrun = ["false"]\nskip_when_staged = true\n',
    )
    assert not run_gate(load_config(repo), repo, staged=False).ok


def test_staged_gate_id_wins_over_staged(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    # An explicit --gate target is the narrower intent and wins over --staged.
    _write_synthetic_catalogue(repo)
    _write_config(
        repo,
        '[[steps]]\nid = "f"\ncatalogue = "scripts.checks.synthetic_cat:ALL_ENTRIES"\n'
        'checks_dir = "scripts/checks"\n',
    )
    run_gate(load_config(repo), repo, gate_id="A1", staged=True)
    out = _plain(capsys.readouterr().out)
    assert "run [A1]" in out
    assert "run [B1]" not in out  # gate_id narrowed, not staged-selected


def test_main_staged_flag_threads_through(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _write_config(
        repo,
        '[[steps]]\nid = "cheap"\nrun = ["true"]\n'
        '[[steps]]\nid = "expensive"\nrun = ["false"]\nskip_when_staged = true\n',
    )
    # Without --staged the failing expensive step gates → exit 1.
    assert main(["run", "--repo-root", str(repo)]) == 1
    # With --staged it is dropped → exit 0.
    assert main(["run", "--repo-root", str(repo), "--staged"]) == 0
    assert "SKIP [expensive]" in _plain(capsys.readouterr().out)


# --------------------------------------------------------------------------- #
# main() — the console entrypoint
# --------------------------------------------------------------------------- #


def test_main_run_returns_zero_on_pass(repo: Path) -> None:
    _write_config(repo, '[[steps]]\nid = "ok"\nrun = ["true"]\n')
    assert main(["run", "--repo-root", str(repo)]) == 0


def test_main_run_returns_one_on_failure(repo: Path) -> None:
    _write_config(repo, '[[steps]]\nid = "bad"\nrun = ["false"]\n')
    assert main(["run", "--repo-root", str(repo)]) == 1


def test_main_missing_config_returns_two(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    rc = main(["run", "--repo-root", str(repo)])
    err = _plain(capsys.readouterr().err)
    assert rc == 2
    assert "fix:" in err


def test_main_run_reads_pyproject_tool_block(repo: Path) -> None:
    # The end-to-end path through a real [tool.tc_fitness] block in pyproject.toml
    # (not the dedicated file) — the canonical consumer shape.
    (repo / "pyproject.toml").write_text(
        "[project]\nname = 'demo'\n\n"
        "[tool.tc_fitness]\n"
        'name = "demo gate"\n\n'
        "[[tool.tc_fitness.steps]]\n"
        'id = "ok"\n'
        'run = ["true"]\n'
    )
    assert main(["run", "--repo-root", str(repo)]) == 0


def test_main_only_flag_threads_through(repo: Path) -> None:
    _write_config(
        repo,
        '[[steps]]\nid = "a"\nrun = ["true"]\n[[steps]]\nid = "b"\nrun = ["false"]\n',
    )
    # --only a skips the failing b → exit 0.
    assert main(["run", "--repo-root", str(repo), "--only", "a"]) == 0
