"""Behavioural tests for the merged lib surface.

Each test pins a behaviour a real call site in kairix or tc-agent-zone depends on,
so the merge is provably behaviour-preserving. Call patterns mirrored:

- kairix: ``gate(name, set, remediation)``, ``main_entry(fn, name, rem, *roots)``,
  ``python_files(*roots)``, ``repo_relative(path)``.
- tc-agent-zone: ``actionable(what, fix, nxt)``, ``emit_failures(name, fails)``,
  ``emit_pass(message)``, ``load_yaml(path) -> (data, err)``,
  ``missing_keys(parsed, required) -> list``.
"""

from __future__ import annotations

import io
from pathlib import Path

import pytest

from tc_fitness.lib import (
    actionable,
    emit_failures,
    emit_pass,
    gate,
    gate_keys,
    load_yaml,
    main_entry,
    missing_keys,
    python_files,
    remediation,
    repo_relative,
)

# --------------------------------------------------------------------------- #
# gate() — kairix baseline-gating contract
# --------------------------------------------------------------------------- #


def test_gate_clean_when_no_violations(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    rc = gate("rule-x", set(), "fix it", repo_root=tmp_path)
    assert rc == 0
    assert "clean" in capsys.readouterr().out


def test_gate_fails_on_net_new_violation(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    rc = gate("rule-x", {Path("kairix/bad.py")}, "REMEDIATION-TEXT", repo_root=tmp_path)
    out = capsys.readouterr().out
    assert rc == 1
    assert "kairix/bad.py" in out
    assert "REMEDIATION-TEXT" in out


def test_gate_grandfathers_baseline_files(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    baseline_dir = tmp_path / ".architecture" / "baseline"
    baseline_dir.mkdir(parents=True)
    (baseline_dir / "rule-x-files.txt").write_text("kairix/legacy.py\n")
    # The same file already in the baseline must NOT trip the gate.
    rc = gate("rule-x", {Path("kairix/legacy.py")}, "fix it", repo_root=tmp_path)
    out = capsys.readouterr().out
    assert rc == 0
    assert "grandfathered" in out
    assert "1 grandfathered" in out


def test_gate_new_violation_alongside_baseline(tmp_path: Path) -> None:
    baseline_dir = tmp_path / ".architecture" / "baseline"
    baseline_dir.mkdir(parents=True)
    (baseline_dir / "rule-x-files.txt").write_text("kairix/legacy.py\n")
    rc = gate("rule-x", {Path("kairix/legacy.py"), Path("kairix/new.py")}, "fix it", repo_root=tmp_path)
    assert rc == 1  # legacy grandfathered, new.py is net-new


def test_gate_baseline_skips_comment_lines(tmp_path: Path) -> None:
    baseline_dir = tmp_path / ".architecture" / "baseline"
    baseline_dir.mkdir(parents=True)
    (baseline_dir / "rule-x-files.txt").write_text("# a comment\nkairix/legacy.py\n")
    rc = gate("rule-x", {Path("kairix/legacy.py")}, "fix it", repo_root=tmp_path)
    assert rc == 0


def test_gate_relativises_absolute_paths(tmp_path: Path) -> None:
    abs_violation = tmp_path / "kairix" / "bad.py"
    abs_violation.parent.mkdir(parents=True)
    abs_violation.write_text("x = 1\n")
    rc = gate("rule-x", {abs_violation}, "fix it", repo_root=tmp_path)
    # Absolute path under repo_root is relativised; with no baseline it's net-new.
    assert rc == 1


# --------------------------------------------------------------------------- #
# gate(fail_on_stale=True) + counts banner — Task 1.7
#
# Generalises taz's per-check stale-baseline behaviour: a baseline entry no
# longer present in the current scan is STALE and FAILs (consumer supplies the
# remediation); on pass the banner reports new-vs-grandfathered counts. The
# default (fail_on_stale=False) preserves the v0.1.0 exit-code contract.
# --------------------------------------------------------------------------- #


def test_gate_fail_on_stale_fails_when_baseline_entry_no_longer_violates(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    baseline_dir = tmp_path / ".architecture" / "baseline"
    baseline_dir.mkdir(parents=True)
    (baseline_dir / "rule-x-files.txt").write_text("kairix/resolved.py\nkairix/still.py\n")
    # Only kairix/still.py still violates → kairix/resolved.py is STALE.
    rc = gate(
        "rule-x",
        {Path("kairix/still.py")},
        "fix it",
        repo_root=tmp_path,
        fail_on_stale=True,
        stale_remediation="REMOVE-STALE-LINE",
    )
    out = capsys.readouterr().out
    assert rc == 1
    assert "kairix/resolved.py" in out
    assert "REMOVE-STALE-LINE" in out
    assert "STALE" in out or "stale" in out


def test_gate_fail_on_stale_passes_and_prints_counts_when_no_stale(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    baseline_dir = tmp_path / ".architecture" / "baseline"
    baseline_dir.mkdir(parents=True)
    (baseline_dir / "rule-x-files.txt").write_text("kairix/a.py\nkairix/b.py\n")
    rc = gate(
        "rule-x",
        {Path("kairix/a.py"), Path("kairix/b.py")},
        "fix it",
        repo_root=tmp_path,
        fail_on_stale=True,
    )
    out = capsys.readouterr().out
    assert rc == 0
    # The counts banner reports new (0) vs grandfathered (2).
    assert "0" in out and "2" in out
    assert "grandfathered" in out


def test_gate_fail_on_stale_default_false_is_unchanged(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # With the default (fail_on_stale=False), a baseline entry that no longer
    # violates is SILENTLY tolerated (the v0.1.0 shrinks-are-clean contract).
    baseline_dir = tmp_path / ".architecture" / "baseline"
    baseline_dir.mkdir(parents=True)
    (baseline_dir / "rule-x-files.txt").write_text("kairix/resolved.py\nkairix/still.py\n")
    rc = gate("rule-x", {Path("kairix/still.py")}, "fix it", repo_root=tmp_path)
    out = capsys.readouterr().out
    assert rc == 0  # stale entry tolerated, no FAIL
    assert "STALE" not in out


def test_gate_net_new_still_fails_under_fail_on_stale(tmp_path: Path) -> None:
    # A net-new violation FAILs regardless of fail_on_stale.
    baseline_dir = tmp_path / ".architecture" / "baseline"
    baseline_dir.mkdir(parents=True)
    (baseline_dir / "rule-x-files.txt").write_text("kairix/legacy.py\n")
    rc = gate(
        "rule-x",
        {Path("kairix/legacy.py"), Path("kairix/new.py")},
        "fix it",
        repo_root=tmp_path,
        fail_on_stale=True,
        stale_remediation="rem",
    )
    assert rc == 1


def test_gate_keys_fail_on_stale(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    baseline_dir = tmp_path / ".architecture" / "baseline"
    baseline_dir.mkdir(parents=True)
    (baseline_dir / "f30-ids.txt").write_text("F30:resolved\nF30:still\n")
    rc = gate_keys(
        "f30",
        {"F30:still"},
        "fix it",
        repo_root=tmp_path,
        fail_on_stale=True,
        stale_remediation="REMOVE-STALE-ID",
    )
    out = capsys.readouterr().out
    assert rc == 1
    assert "F30:resolved" in out
    assert "REMOVE-STALE-ID" in out


def test_gate_keys_fail_on_stale_default_false_unchanged(tmp_path: Path) -> None:
    baseline_dir = tmp_path / ".architecture" / "baseline"
    baseline_dir.mkdir(parents=True)
    (baseline_dir / "f30-ids.txt").write_text("F30:resolved\nF30:still\n")
    rc = gate_keys("f30", {"F30:still"}, "fix it", repo_root=tmp_path)
    assert rc == 0  # shrinks-are-clean by default


# --------------------------------------------------------------------------- #
# python_files() / repo_relative() / main_entry() — kairix enumeration
# --------------------------------------------------------------------------- #


def test_python_files_finds_py_skips_pycache(tmp_path: Path) -> None:
    (tmp_path / "kairix").mkdir()
    (tmp_path / "kairix" / "a.py").write_text("")
    (tmp_path / "kairix" / "nested").mkdir()
    (tmp_path / "kairix" / "nested" / "b.py").write_text("")
    (tmp_path / "kairix" / "__pycache__").mkdir()
    (tmp_path / "kairix" / "__pycache__" / "c.py").write_text("")
    (tmp_path / "kairix" / "notpy.txt").write_text("")

    found = {p.name for p in python_files("kairix", repo_root=tmp_path)}
    assert found == {"a.py", "b.py"}


def test_python_files_skips_missing_root(tmp_path: Path) -> None:
    assert python_files("does-not-exist", repo_root=tmp_path) == []


def test_repo_relative_strips_root(tmp_path: Path) -> None:
    target = tmp_path / "kairix" / "x.py"
    target.parent.mkdir(parents=True)
    target.write_text("")
    assert repo_relative(target, repo_root=tmp_path) == Path("kairix/x.py")


def test_main_entry_gates_on_check_fn(tmp_path: Path) -> None:
    (tmp_path / "kairix").mkdir()
    good = tmp_path / "kairix" / "good.py"
    bad = tmp_path / "kairix" / "bad.py"
    good.write_text("clean\n")
    bad.write_text("VIOLATION\n")

    def check_fn(path: Path) -> bool:
        return "VIOLATION" in path.read_text()

    rc = main_entry(check_fn, "rule-y", "fix it", "kairix", repo_root=tmp_path)
    assert rc == 1  # bad.py flagged, net-new


def test_main_entry_clean_when_check_fn_never_fires(tmp_path: Path) -> None:
    (tmp_path / "kairix").mkdir()
    (tmp_path / "kairix" / "good.py").write_text("clean\n")
    rc = main_entry(lambda p: False, "rule-y", "fix it", "kairix", repo_root=tmp_path)
    assert rc == 0


# --------------------------------------------------------------------------- #
# actionable() — tc-agent-zone canonical FAIL shape
# --------------------------------------------------------------------------- #


def test_actionable_shape() -> None:
    assert actionable("X broke", "do Y", "rerun Z") == "X broke; fix: do Y; next: rerun Z"


def test_actionable_v01_default_is_byte_identical_two_marker() -> None:
    # v0.1.0 contract: with run omitted the output is EXACTLY the 2-marker form.
    # Proves the new optional param did not perturb existing call sites.
    assert actionable("kairix/x.py leaks", "redact it", "re-run check") == (
        "kairix/x.py leaks; fix: redact it; next: re-run check"
    )
    # The positional call shape kairix uses everywhere still emits no run: marker.
    assert "; run:" not in actionable("a", "b", "c")


def test_actionable_run_appends_third_marker() -> None:
    # taz's 59 fix/next/run checks: the third marker is appended verbatim.
    assert actionable("X broke", "do Y", "rerun Z", "python check.py") == (
        "X broke; fix: do Y; next: rerun Z; run: python check.py"
    )


def test_actionable_run_can_be_keyword() -> None:
    assert actionable("X", "Y", "Z", run="cmd") == "X; fix: Y; next: Z; run: cmd"


def test_actionable_run_explicit_none_matches_default() -> None:
    assert actionable("X", "Y", "Z", run=None) == actionable("X", "Y", "Z")


# --------------------------------------------------------------------------- #
# remediation() — taz multiline fix:/next:/run: (+ Pass/Forbidden) block
# --------------------------------------------------------------------------- #


def test_remediation_three_markers_on_own_lines() -> None:
    block = remediation("redact it", "re-run check", "python check.py")
    assert block == "fix: redact it\nnext: re-run check\nrun: python check.py"


def test_remediation_with_pass_and_forbidden_examples() -> None:
    block = remediation(
        "redact the secret",
        "re-run the check",
        "python scripts/checks/check_f15.py",
        passing='logger.info("token redacted")',
        forbidden='logger.info(f"token={token}")',
    )
    assert block == (
        "fix: redact the secret\n"
        "next: re-run the check\n"
        "run: python scripts/checks/check_f15.py\n"
        'Pass: logger.info("token redacted")\n'
        'Forbidden: logger.info(f"token={token}")'
    )


def test_remediation_pass_only_omits_forbidden() -> None:
    block = remediation("a", "b", "c", passing="good")
    assert block == "fix: a\nnext: b\nrun: c\nPass: good"
    assert "Forbidden:" not in block


def test_remediation_forbidden_only_omits_pass() -> None:
    block = remediation("a", "b", "c", forbidden="bad")
    assert block == "fix: a\nnext: b\nrun: c\nForbidden: bad"
    assert "Pass:" not in block


# --------------------------------------------------------------------------- #
# gate_keys() — taz string-keyed baseline ratchet (-ids.txt / -paths.txt)
# --------------------------------------------------------------------------- #


def test_gate_keys_clean_when_no_violations(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    rc = gate_keys("rule-id", set(), "fix it", repo_root=tmp_path)
    assert rc == 0
    assert "clean" in capsys.readouterr().out


def test_gate_keys_fails_on_net_new_logical_id(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    rc = gate_keys("f30", {"F30:my_new_tool"}, "REMEDIATION-TEXT", repo_root=tmp_path)
    out = capsys.readouterr().out
    assert rc == 1
    assert "F30:my_new_tool" in out
    assert "REMEDIATION-TEXT" in out


def test_gate_keys_grandfathers_baseline_ids(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    baseline_dir = tmp_path / ".architecture" / "baseline"
    baseline_dir.mkdir(parents=True)
    (baseline_dir / "f30-ids.txt").write_text("F30:legacy_tool\n")
    rc = gate_keys("f30", {"F30:legacy_tool"}, "fix it", repo_root=tmp_path)
    out = capsys.readouterr().out
    assert rc == 0
    assert "1 grandfathered" in out


def test_gate_keys_new_id_alongside_baseline(tmp_path: Path) -> None:
    baseline_dir = tmp_path / ".architecture" / "baseline"
    baseline_dir.mkdir(parents=True)
    (baseline_dir / "f30-ids.txt").write_text("F30:legacy_tool\n")
    rc = gate_keys("f30", {"F30:legacy_tool", "F30:new_tool"}, "fix it", repo_root=tmp_path)
    assert rc == 1  # legacy grandfathered, new_tool is net-new


def test_gate_keys_baseline_skips_comment_lines(tmp_path: Path) -> None:
    baseline_dir = tmp_path / ".architecture" / "baseline"
    baseline_dir.mkdir(parents=True)
    (baseline_dir / "f30-ids.txt").write_text("# a comment\nF30:legacy_tool\n")
    rc = gate_keys("f30", {"F30:legacy_tool"}, "fix it", repo_root=tmp_path)
    assert rc == 0


def test_gate_keys_shrinks_only_is_clean(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    # Baseline has two ids; current has one (a resolved id). No net-new ⇒ clean pass.
    baseline_dir = tmp_path / ".architecture" / "baseline"
    baseline_dir.mkdir(parents=True)
    (baseline_dir / "f30-ids.txt").write_text("F30:a\nF30:b\n")
    rc = gate_keys("f30", {"F30:a"}, "fix it", repo_root=tmp_path)
    assert rc == 0
    assert "grandfathered" in capsys.readouterr().out


def test_gate_keys_paths_suffix_selects_paths_baseline(tmp_path: Path) -> None:
    # A path-glob key set ratchets against -paths.txt when the suffix is overridden.
    baseline_dir = tmp_path / ".architecture" / "baseline"
    baseline_dir.mkdir(parents=True)
    (baseline_dir / "f89-paths.txt").write_text("kairix/**/web/static/*\n")
    rc = gate_keys(
        "f89",
        {"kairix/**/web/static/*"},
        "fix it",
        repo_root=tmp_path,
        baseline_suffix="-paths.txt",
    )
    assert rc == 0


def test_gate_keys_does_not_relativise_keys(tmp_path: Path) -> None:
    # A key that looks like an absolute path must be treated as an OPAQUE string,
    # NOT relativised the way gate() relativises real Paths. Same string in the
    # baseline ⇒ grandfathered (proves no Path coercion happens).
    baseline_dir = tmp_path / ".architecture" / "baseline"
    baseline_dir.mkdir(parents=True)
    abs_like = "/abs/looking/key"
    (baseline_dir / "rule-ids.txt").write_text(f"{abs_like}\n")
    rc = gate_keys("rule", {abs_like}, "fix it", repo_root=tmp_path)
    assert rc == 0


# --------------------------------------------------------------------------- #
# emit_failures() / emit_pass() — tc-agent-zone banners
# --------------------------------------------------------------------------- #


def test_emit_failures_banner_and_bullets() -> None:
    buf = io.StringIO()
    emit_failures("my_check", ["first fail", "second fail"], stream=buf)
    text = buf.getvalue()
    assert "FAIL my_check (2 violations)" in text
    assert "  - first fail" in text
    assert "  - second fail" in text


def test_emit_failures_defaults_to_stderr(capsys: pytest.CaptureFixture[str]) -> None:
    emit_failures("my_check", ["boom"])
    captured = capsys.readouterr()
    assert "FAIL my_check (1 violations)" in captured.err
    assert captured.out == ""


def test_emit_pass_writes_message() -> None:
    buf = io.StringIO()
    emit_pass("PASS my_check", stream=buf)
    assert buf.getvalue().strip() == "PASS my_check"


def test_emit_pass_defaults_to_stdout(capsys: pytest.CaptureFixture[str]) -> None:
    emit_pass("PASS my_check")
    captured = capsys.readouterr()
    assert "PASS my_check" in captured.out
    assert captured.err == ""


# --------------------------------------------------------------------------- #
# load_yaml() — tc-agent-zone (data, error) contract
# --------------------------------------------------------------------------- #


def test_load_yaml_success(tmp_path: Path) -> None:
    pytest.importorskip("yaml")
    f = tmp_path / "ok.yaml"
    f.write_text("a: 1\nb: two\n")
    data, err = load_yaml(f)
    assert err is None
    assert data == {"a": 1, "b": "two"}


def test_load_yaml_empty_returns_empty_dict(tmp_path: Path) -> None:
    pytest.importorskip("yaml")
    f = tmp_path / "empty.yaml"
    f.write_text("")
    data, err = load_yaml(f)
    assert err is None
    assert data == {}


def test_load_yaml_malformed_returns_error(tmp_path: Path) -> None:
    pytest.importorskip("yaml")
    f = tmp_path / "bad.yaml"
    f.write_text("a: [unterminated\n")
    data, err = load_yaml(f)
    assert data is None
    assert err is not None
    assert "invalid YAML" in err


# --------------------------------------------------------------------------- #
# missing_keys() — tc-agent-zone required-key contract
# --------------------------------------------------------------------------- #


def test_missing_keys_reports_absent() -> None:
    assert missing_keys({"a": 1}, ("a", "b", "c")) == ["b", "c"]


def test_missing_keys_empty_when_all_present() -> None:
    assert missing_keys({"a": 1, "b": 2}, ("a", "b")) == []


def test_missing_keys_preserves_required_order() -> None:
    assert missing_keys({}, ("z", "a", "m")) == ["z", "a", "m"]
