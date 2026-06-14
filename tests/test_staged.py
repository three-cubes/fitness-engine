"""Soundness battery for the staged-selection logic.

The non-negotiable property is **no false negative on a staged change**: if
staging a file could newly violate rule R, ``--staged`` MUST run R. These tests
prove the three selection classes (file-local / relational / always-run), the
scope-derivation hook, the fail-safe "run when scope unresolved" residue, the
file-local narrowing through a real enumeration, and the transparent staged
ledger end-to-end through the runner.
"""

from __future__ import annotations

from pathlib import Path

import tc_fitness
from tc_fitness.catalogue import RuleEntry
from tc_fitness.runner import run
from tc_fitness.staged import (
    decide,
    filter_to_staged,
    resolve_staged_scope,
    restrict_python_files,
    staged_abs_set,
    staged_in_scope,
)

# --------------------------------------------------------------------------- #
# scope resolution: explicit wins, else resolver, else None (fail-safe)
# --------------------------------------------------------------------------- #


def test_explicit_staged_scope_wins() -> None:
    entry = RuleEntry(id="F", gate="f", check="x", staged_scope=("kairix",))
    # Resolver would say something else, but explicit scope is the source of truth.
    assert resolve_staged_scope(entry, "check_x.py", resolver=lambda _s: ("tests",)) == ("kairix",)


def test_derived_scope_via_resolver() -> None:
    entry = RuleEntry(id="F", gate="f", check="x")  # no explicit scope
    assert resolve_staged_scope(entry, "check_x.py", resolver=lambda _s: ("kairix/core",)) == (
        "kairix/core",
    )


def test_no_resolver_no_explicit_scope_is_none() -> None:
    entry = RuleEntry(id="F", gate="f", check="x")
    assert resolve_staged_scope(entry, "check_x.py", resolver=None) is None


# --------------------------------------------------------------------------- #
# staged_in_scope path-prefix matching
# --------------------------------------------------------------------------- #


def test_staged_in_scope_directory_prefix() -> None:
    scope = ("kairix",)
    staged = ["kairix/core/x.py", "tests/test_x.py", "kairixx/sneaky.py"]
    # "kairix" matches kairix/... but NOT kairixx (prefix boundary).
    assert staged_in_scope(scope, staged) == ["kairix/core/x.py"]


def test_staged_in_scope_exact_file_prefix() -> None:
    scope = ("kairix/cli.py",)
    assert staged_in_scope(scope, ["kairix/cli.py"]) == ["kairix/cli.py"]
    assert staged_in_scope(scope, ["kairix/cli_helpers.py"]) == []


def test_staged_in_scope_none_is_everything() -> None:
    staged = ["a", "b"]
    assert staged_in_scope(None, staged) == staged


# --------------------------------------------------------------------------- #
# decide() — the three classes, soundness
# --------------------------------------------------------------------------- #


def test_empty_staged_runs_everything() -> None:
    # The pre-commit --all-files quirk: no staged paths ⇒ run everything.
    entry = RuleEntry(id="F", gate="f", check="x", staged_class="file-local", staged_scope=("kairix",))
    assert decide(entry, "check_x.py", []).run is True


def test_always_run_always_dispatches() -> None:
    entry = RuleEntry(id="F50", gate="f50", check="x", staged_class="always-run")
    # Even a totally unrelated staged file runs an always-run rule.
    d = decide(entry, "check_x.py", ["totally/unrelated.txt"])
    assert d.run is True
    assert "always-run" in d.reason


def test_file_local_runs_only_on_in_scope_staged_file() -> None:
    entry = RuleEntry(id="F", gate="f", check="x", staged_class="file-local", staged_scope=("kairix",))
    # In scope → run, and the staged subset is handed back for narrowing.
    in_scope = decide(entry, "check_x.py", ["kairix/a.py", "docs/readme.md"])
    assert in_scope.run is True
    assert in_scope.scope_files == ("kairix/a.py",)
    # Out of scope → skip.
    out_scope = decide(entry, "check_x.py", ["docs/readme.md"])
    assert out_scope.run is False


def test_file_local_unresolved_scope_runs_fail_safe() -> None:
    # SOUNDNESS: a file-local rule whose scope can't be resolved must RUN
    # (never silently skip) when there ARE staged paths.
    entry = RuleEntry(id="F", gate="f", check="x", staged_class="file-local")
    d = decide(entry, "check_x.py", ["anything.py"], resolver=lambda _s: None)
    assert d.run is True
    assert "fail-safe" in d.reason


def test_relational_runs_full_scope_when_any_path_in_scope() -> None:
    entry = RuleEntry(
        id="F30",
        gate="f30",
        check="x",
        staged_class="relational",
        staged_scope=("kairix/cli.py", "tests"),
    )
    # A staged TEST deletion (relational trigger) runs the FULL scope — and
    # crucially returns NO scope_files, so the rule is NOT narrowed.
    d = decide(entry, "check_x.py", ["tests/test_thing.py"])
    assert d.run is True
    assert d.scope_files is None
    # A path outside the relational scope → skip.
    assert decide(entry, "check_x.py", ["docs/x.md"]).run is False


def test_relational_unresolved_scope_runs_when_touched() -> None:
    # A relational rule with an unresolved scope treats ALL staged paths as in
    # scope (staged_in_scope(None) returns everything) → runs.
    entry = RuleEntry(id="F", gate="f", check="x", staged_class="relational")
    d = decide(entry, "check_x.py", ["whatever.py"], resolver=lambda _s: None)
    assert d.run is True


# --------------------------------------------------------------------------- #
# file-local narrowing through a real enumeration
# --------------------------------------------------------------------------- #


def test_restrict_python_files_narrows_to_staged(tmp_path: Path) -> None:
    (tmp_path / "kairix").mkdir()
    a = tmp_path / "kairix" / "a.py"
    b = tmp_path / "kairix" / "b.py"
    a.write_text("")
    b.write_text("")

    # Outside the context: both files enumerate.
    full = tc_fitness.python_files("kairix", repo_root=tmp_path)
    assert {p.name for p in full} == {"a.py", "b.py"}

    # Inside the context: only the staged file (a.py) enumerates.
    with restrict_python_files(tmp_path, ["kairix/a.py"]):
        narrowed = tc_fitness.python_files("kairix", repo_root=tmp_path)
    assert {p.name for p in narrowed} == {"a.py"}

    # Restored on exit.
    assert {p.name for p in tc_fitness.python_files("kairix", repo_root=tmp_path)} == {"a.py", "b.py"}


def test_filter_to_staged_keeps_only_staged(tmp_path: Path) -> None:
    a = tmp_path / "a.py"
    b = tmp_path / "b.py"
    a.write_text("")
    b.write_text("")
    staged_abs = staged_abs_set(tmp_path, ["a.py"])
    assert filter_to_staged([a, b], staged_abs) == [a]


# --------------------------------------------------------------------------- #
# end-to-end staged dispatch through the runner — the transparent ledger
# --------------------------------------------------------------------------- #


def _write_py_check(checks_dir: Path, name: str, body: str) -> None:
    (checks_dir / f"check_{name}.py").write_text(
        "def main():\n" + "\n".join(f"    {line}" for line in body.splitlines()) + "\n"
    )


def test_staged_dispatch_skips_out_of_scope_transparently(tmp_path: Path) -> None:
    checks_dir = tmp_path / "scripts" / "checks"
    checks_dir.mkdir(parents=True)
    _write_py_check(checks_dir, "kairix_rule", "return 0")
    _write_py_check(checks_dir, "tests_rule", "return 1")  # would FAIL if dispatched

    rules = (
        RuleEntry(
            id="K", gate="k", check="kairix_rule", summary="kairix rule",
            staged_class="file-local", staged_scope=("kairix",),
        ),
        RuleEntry(
            id="T", gate="t", check="tests_rule", summary="tests rule",
            staged_class="file-local", staged_scope=("tests",),
        ),
    )

    # Only a kairix file is staged → the tests rule must be skipped (so its FAIL
    # never registers), the kairix rule runs.
    import io
    import re
    from contextlib import redirect_stdout

    buf = io.StringIO()
    with redirect_stdout(buf):
        verdict = run(
            rules,
            mode="staged",
            staged_files=["kairix/a.py"],
            repo_root=tmp_path,
            checks_dir=checks_dir,
        )
    out = re.sub(r"\x1b\[[0-9;]*m", "", buf.getvalue())

    assert verdict.ok  # tests_rule (which would fail) was correctly skipped
    assert verdict.ran == 1
    assert verdict.skipped == 1
    assert "run [K]" in out
    assert "skip [T]" in out
    assert "no staged file in scope" in out
    assert "staged selection: 1 ran, 1 skipped" in out


def test_staged_dispatch_no_false_negative(tmp_path: Path) -> None:
    # SOUNDNESS end-to-end: a staged file IN a failing rule's scope MUST run the
    # rule and surface the failure (the property that makes --staged trustworthy).
    checks_dir = tmp_path / "scripts" / "checks"
    checks_dir.mkdir(parents=True)
    _write_py_check(checks_dir, "guard", "return 1")  # always fails when run

    rules = (
        RuleEntry(
            id="GUARD", gate="guard", check="guard", summary="guard rule",
            staged_class="file-local", staged_scope=("kairix",),
        ),
    )
    verdict = run(
        rules,
        mode="staged",
        staged_files=["kairix/touched.py"],
        repo_root=tmp_path,
        checks_dir=checks_dir,
    )
    assert verdict.failures == ["GUARD"]  # the staged change tripped the rule


def test_staged_empty_runs_everything_through_runner(tmp_path: Path) -> None:
    checks_dir = tmp_path / "scripts" / "checks"
    checks_dir.mkdir(parents=True)
    _write_py_check(checks_dir, "any", "return 1")
    rules = (
        RuleEntry(
            id="ANY", gate="any", check="any", summary="any",
            staged_class="file-local", staged_scope=("nowhere",),
        ),
    )
    # No staged files at all → fail-safe: the rule runs even though its scope
    # doesn't match (the pre-commit --all-files quirk).
    verdict = run(
        rules, mode="staged", staged_files=[], repo_root=tmp_path, checks_dir=checks_dir
    )
    assert verdict.failures == ["ANY"]
