"""Tests for the CORE check deterministic_tests (SGO-200).

Two layers of proof:

* the pure detection core (parse / plan / compare / argv) driven directly and
  with an injected fake runner — no subprocess, fast and hermetic;
* end-to-end FIXTURES that prove the gate BITES: a genuinely non-deterministic
  test (a counter that flips even/odd each run) is caught by the fixed-seed
  repeat probe, and a deliberately order-dependent pair (shared module state)
  is caught by the order probe — both surfacing the offending test id.
"""

from __future__ import annotations

import sys
from pathlib import Path

from _core_check_assertions import assert_no_repo_identity

from tc_fitness.core_checks.deterministic_tests import (
    DeterministicTests,
    Divergence,
    RunSpec,
    SuiteRunError,
    build,
    build_pytest_argv,
    compare_runs,
    detect_nondeterminism,
    format_failure,
    main,
    parse_collected,
    parse_outcomes,
    plan_runs,
    shuffled_order,
)

# --------------------------------------------------------------------------- #
# Pure helpers.
# --------------------------------------------------------------------------- #

_PYTEST_V_OUTPUT = """\
tests/test_a.py::test_one PASSED                                         [ 25%]
tests/test_a.py::TestCls::test_m FAILED                                  [ 50%]
tests/test_b.py::test_p[1] PASSED                                        [ 75%]
tests/test_b.py::test_p[2] SKIPPED                                       [100%]
=========================== short test summary info ============================
FAILED tests/test_a.py::TestCls::test_m - assert True is False
"""


def test_parse_outcomes_reads_progress_lines_only() -> None:
    outcomes = parse_outcomes(_PYTEST_V_OUTPUT)
    assert outcomes == {
        "tests/test_a.py::test_one": "passed",
        "tests/test_a.py::TestCls::test_m": "failed",
        "tests/test_b.py::test_p[1]": "passed",
        "tests/test_b.py::test_p[2]": "skipped",
    }
    # The terminal-summary "FAILED <nodeid> - ..." line is NOT double-counted as
    # a result (it carries no [ NN%] progress marker).


def test_parse_collected_keeps_node_ids_only() -> None:
    text = "tests/test_a.py::test_one\ntests/test_b.py::test_p[1]\n\n2 tests collected in 0.01s\n"
    assert parse_collected(text) == [
        "tests/test_a.py::test_one",
        "tests/test_b.py::test_p[1]",
    ]


def test_shuffled_order_is_deterministic_in_seed() -> None:
    ids = [f"t{i}" for i in range(12)]
    assert shuffled_order(ids, 7) == shuffled_order(ids, 7)
    # A different seed generally yields a different order (not the natural one).
    assert shuffled_order(ids, 7) != ids


def test_plan_runs_shape() -> None:
    plan = plan_runs(2, [1, 2])
    assert [s.label for s in plan] == ["fixed-seed:rep1", "fixed-seed:rep2", "order:seed1", "order:seed2"]
    assert [s.order_seed for s in plan] == [None, None, 1, 2]


def test_compare_runs_flags_only_unstable() -> None:
    runs = [
        ("rep1", {"a": "passed", "b": "passed"}),
        ("rep2", {"a": "failed", "b": "passed"}),
    ]
    diffs = compare_runs(runs)
    assert [d.test_id for d in diffs] == ["a"]
    assert diffs[0].outcomes == (("rep1", "passed"), ("rep2", "failed"))


def test_compare_runs_flags_absence_as_instability() -> None:
    runs = [("r1", {"a": "passed"}), ("r2", {})]
    diffs = compare_runs(runs)
    assert [d.test_id for d in diffs] == ["a"]
    assert diffs[0].outcomes == (("r1", "passed"), ("r2", "<absent>"))


def test_compare_runs_stable_suite_is_clean() -> None:
    runs = [("r1", {"a": "passed", "b": "skipped"}), ("r2", {"a": "passed", "b": "skipped"})]
    assert compare_runs(runs) == []


def test_build_argv_plugin_free_blocks_reruns_and_randomly() -> None:
    argv = build_pytest_argv(
        ["python", "-m", "pytest"],
        test_paths=["tests"],
        node_ids=["tests/test_a.py::t"],
        order_seed=None,
        use_randomly=False,
    )
    # The reconciliation with the loop guardrail: reruns can NEVER mask a flake
    # inside the probe, and no run ever asks for retries.
    assert "no:rerunfailures" in argv
    assert "no:randomly" in argv
    assert not any(a.startswith("--reruns") for a in argv)
    assert "--randomly-seed" not in argv
    # A natural-order run scopes by path, not explicit node ids.
    assert argv[-1] == "tests"


def test_build_argv_order_run_uses_shuffled_node_ids() -> None:
    node_ids = [f"tests/test_a.py::t{i}" for i in range(8)]
    argv = build_pytest_argv(
        ["python", "-m", "pytest"],
        test_paths=["tests"],
        node_ids=node_ids,
        order_seed=3,
        use_randomly=False,
    )
    tail = argv[argv.index("no:randomly") + 1 :]
    assert sorted(tail) == sorted(node_ids)
    assert tail == shuffled_order(node_ids, 3)


def test_build_argv_randomly_mode_delegates_ordering() -> None:
    argv = build_pytest_argv(
        ["python", "-m", "pytest"],
        test_paths=["tests"],
        node_ids=[],
        order_seed=5,
        use_randomly=True,
    )
    assert "--randomly-seed" in argv
    assert argv[argv.index("--randomly-seed") + 1] == "5"
    assert "no:randomly" not in argv  # not disabled — we WANT randomly here
    assert "no:rerunfailures" in argv  # still never masked by reruns


def test_detect_nondeterminism_with_injected_runner() -> None:
    # rep1/rep2 identical; the order run flips "b" — the order probe bites.
    scripted = {
        "fixed-seed:rep1": {"a": "passed", "b": "passed"},
        "fixed-seed:rep2": {"a": "passed", "b": "passed"},
        "order:seed1": {"a": "passed", "b": "failed"},
    }
    plan = [RunSpec("fixed-seed:rep1", None), RunSpec("fixed-seed:rep2", None), RunSpec("order:seed1", 1)]
    diffs = detect_nondeterminism(plan, lambda spec: scripted[spec.label])
    assert [d.test_id for d in diffs] == ["b"]


def test_format_failure_names_offender_and_remediation() -> None:
    block = format_failure(
        [Divergence("tests/test_x.py::test_flaky", (("rep1", "passed"), ("rep2", "failed")))]
    )
    assert "FAIL [deterministic-tests]" in block
    assert "tests/test_x.py::test_flaky" in block
    assert "rep1=passed" in block and "rep2=failed" in block
    assert "--reruns" in block  # remediation forbids the retry escape hatch


# --------------------------------------------------------------------------- #
# Config injection + conformance.
# --------------------------------------------------------------------------- #


def test_build_returns_rule_with_defaults() -> None:
    rule = build({})
    assert isinstance(rule, DeterministicTests)
    assert rule.repeats == 2
    assert rule.use_randomly is False


def test_from_config_reads_knobs() -> None:
    rule = build(
        {
            "roots": ["tests"],
            "seed": 42,
            "repeats": 3,
            "order_seeds": [9, 10],
            "test_command": ["uv", "run", "pytest"],
            "use_randomly": True,
            "timeout_seconds": 120,
        }
    )
    assert rule.seed == 42
    assert rule.repeats == 3
    assert rule.order_seeds == (9, 10)
    assert rule.test_command == ("uv", "run", "pytest")
    assert rule.use_randomly is True
    assert rule.timeout_seconds == 120


def test_no_config_is_vacuous_pass(tmp_path: Path, capsys: object) -> None:
    # No roots configured → nothing to run → vacuous pass (adoption default).
    assert build({}, repo_root=tmp_path).run() == 0


def test_no_repo_strings_in_executable_code() -> None:
    import tc_fitness.core_checks.deterministic_tests as mod

    assert_no_repo_identity(mod.__file__)


def test_suite_run_error_surfaces_as_fail(tmp_path: Path, capsys: object) -> None:
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_ok.py").write_text("def test_ok():\n    assert True\n", encoding="utf-8")

    class _Boom(DeterministicTests):
        def _runner(self, test_paths: object, node_ids: object) -> object:  # type: ignore[override]
            def _run(spec: RunSpec) -> dict[str, str]:
                raise SuiteRunError("boom")

            return _run

    rule = _Boom(repo_root=tmp_path, roots=("tests",))
    rule.order_seeds = ()
    assert rule.run() == 1


# --------------------------------------------------------------------------- #
# End-to-end fixtures: the gate BITES on a real subprocess pytest run.
# --------------------------------------------------------------------------- #


def _seed_tests(tmp_path: Path) -> Path:
    tests = tmp_path / "tests"
    tests.mkdir()
    return tests


def _rule_for(tmp_path: Path, *, repeats: int, order_seeds: tuple[int, ...]) -> DeterministicTests:
    rule = build(
        {
            "roots": ["tests"],
            "repeats": repeats,
            "order_seeds": list(order_seeds),
            # Use THIS interpreter's pytest so the subprocess is hermetic.
            "test_command": [sys.executable, "-m", "pytest"],
            "timeout_seconds": 120,
        },
        repo_root=tmp_path,
    )
    return rule


def test_fixed_seed_repeat_catches_flaky_test(tmp_path: Path, capsys: object) -> None:
    """A test that flips outcome each run is caught by the repeat probe."""
    counter = tmp_path / "counter.txt"
    tests = _seed_tests(tmp_path)
    (tests / "test_flaky.py").write_text(
        "from pathlib import Path\n"
        f"_C = Path(r'{counter}')\n"
        "def test_flaky_counter():\n"
        "    n = int(_C.read_text()) if _C.exists() else 0\n"
        "    _C.write_text(str(n + 1))\n"
        "    assert n % 2 == 0\n",
        encoding="utf-8",
    )
    # Repeat-only: run twice in natural order, no order probe needed.
    rule = _rule_for(tmp_path, repeats=2, order_seeds=())
    rc = rule.run()
    out = capsys.readouterr().out  # type: ignore[attr-defined]
    assert rc == 1, "a test that flips outcome across identical runs must FAIL the gate"
    assert "FAIL [deterministic-tests]" in out
    assert "test_flaky_counter" in out


def test_order_probe_catches_order_dependent_test(tmp_path: Path, capsys: object) -> None:
    """A pair leaking module state across tests is caught by the order probe."""
    tests = _seed_tests(tmp_path)
    # A shared, non-test module (no test_ prefix) both tests mutate/read. In
    # pytest's default prepend import mode the tests/ dir is on sys.path, so
    # `import _shared` resolves to one module instance shared within a run.
    (tests / "_shared.py").write_text("polluted = False\n", encoding="utf-8")
    (tests / "test_a_pollute.py").write_text(
        "import _shared\ndef test_pollute():\n    _shared.polluted = True\n    assert True\n",
        encoding="utf-8",
    )
    (tests / "test_z_depends.py").write_text(
        "import _shared\ndef test_depends_on_clean():\n    assert _shared.polluted is False\n",
        encoding="utf-8",
    )

    # Pick an order seed that puts the dependent test BEFORE the polluter (so it
    # passes), contrasting with the natural order (polluter first → it fails).
    node_ids = [
        "tests/test_a_pollute.py::test_pollute",
        "tests/test_z_depends.py::test_depends_on_clean",
    ]
    swap_seed = next(
        s for s in range(100) if shuffled_order(node_ids, s)[0].endswith("test_depends_on_clean")
    )

    # One natural run + one swapped-order run: the dependent test's verdict flips.
    rule = _rule_for(tmp_path, repeats=1, order_seeds=(swap_seed,))
    rc = rule.run()
    out = capsys.readouterr().out  # type: ignore[attr-defined]
    assert rc == 1, "an order-dependent test must FAIL the determinism gate"
    assert "FAIL [deterministic-tests]" in out
    assert "test_depends_on_clean" in out


def test_stable_suite_passes(tmp_path: Path, capsys: object) -> None:
    """A genuinely independent suite passes under repeats + order probes."""
    tests = _seed_tests(tmp_path)
    (tests / "test_indep.py").write_text(
        "def test_a():\n    assert 1 + 1 == 2\n\n\ndef test_b():\n    assert 'x' in 'xyz'\n",
        encoding="utf-8",
    )
    rule = _rule_for(tmp_path, repeats=2, order_seeds=(1, 2))
    rc = rule.run()
    out = capsys.readouterr().out  # type: ignore[attr-defined]
    assert rc == 0, "a deterministic suite must PASS"
    assert "ok [deterministic-tests]" in out


def test_main_establish_baseline_is_noop_zero(tmp_path: Path, capsys: object) -> None:
    # A determinism gate has no per-file baseline; establish mode is a harmless
    # zero-exit no-op (nothing to grandfather), keeping the adoption contract.
    rc = main(["--establish-baseline", "--repo-root", str(tmp_path)])
    assert rc == 0
