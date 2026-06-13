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
    load_yaml,
    main_entry,
    missing_keys,
    python_files,
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
