"""Tests for the repo-agnostic, config-driven FitnessRule ABC (v0.6.0)."""

from __future__ import annotations

from pathlib import Path

import pytest

from tc_fitness.fitness_rule import FitnessRule


class _BadWord(FitnessRule):
    """A trivial concrete rule: a ``.py`` file containing the token ``BADWORD``."""

    name = "bad-word"
    remediation = "fix: remove BADWORD; next: re-run; run: pytest"
    extensions = (".py",)

    def file_has_violation(self, path: Path) -> bool:
        return "BADWORD" in path.read_text(encoding="utf-8")


def _seed(tmp_path: Path, rel: str, body: str) -> None:
    p = tmp_path / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body, encoding="utf-8")


def test_abstract_cannot_instantiate() -> None:
    with pytest.raises(TypeError):
        FitnessRule()  # type: ignore[abstract]


def test_collect_violations_respects_config_roots(tmp_path: Path) -> None:
    _seed(tmp_path, "src/bad.py", "x = 'BADWORD'\n")
    _seed(tmp_path, "other/bad.py", "x = 'BADWORD'\n")
    rule = _BadWord(repo_root=tmp_path, roots=("src",))
    violations = {str(p) for p in rule.collect_violations()}
    assert violations == {"src/bad.py"}  # 'other/' is out of configured scope


def test_from_config_overrides_roots_and_exempt(tmp_path: Path) -> None:
    _seed(tmp_path, "src/a.py", "x = 'BADWORD'\n")
    _seed(tmp_path, "src/b.py", "x = 'BADWORD'\n")
    rule = _BadWord.from_config(
        {"roots": ["src"], "exempt_files": ["src/b.py"]},
        repo_root=tmp_path,
    )
    assert {str(p) for p in rule.collect_violations()} == {"src/a.py"}


def test_extension_filter(tmp_path: Path) -> None:
    _seed(tmp_path, "src/a.py", "BADWORD")
    _seed(tmp_path, "src/a.txt", "BADWORD")
    rule = _BadWord(repo_root=tmp_path, roots=("src",))
    assert {str(p) for p in rule.collect_violations()} == {"src/a.py"}


def test_run_clean_when_no_baseline_and_no_violation(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _seed(tmp_path, "src/ok.py", "x = 1\n")
    rule = _BadWord(repo_root=tmp_path, roots=("src",))
    assert rule.run() == 0


def test_run_fails_on_net_new_violation(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _seed(tmp_path, "src/bad.py", "BADWORD\n")
    rule = _BadWord(repo_root=tmp_path, roots=("src",))
    assert rule.run() == 1


def test_establish_then_violation_is_grandfathered(tmp_path: Path) -> None:
    _seed(tmp_path, "src/bad.py", "BADWORD\n")
    rule = _BadWord(repo_root=tmp_path, roots=("src",))
    rule.establish_baseline()
    # Same offender is now grandfathered → clean.
    assert rule.run() == 0
    assert rule.load_baseline() == {"src/bad.py"}


def test_grandfathered_does_not_mask_a_new_offender(tmp_path: Path) -> None:
    _seed(tmp_path, "src/old.py", "BADWORD\n")
    rule = _BadWord(repo_root=tmp_path, roots=("src",))
    rule.establish_baseline()
    _seed(tmp_path, "src/new.py", "BADWORD\n")
    assert rule.run() == 1  # net-new offender still fails


def test_name_override_via_config(tmp_path: Path) -> None:
    rule = _BadWord.from_config({"name": "renamed"}, repo_root=tmp_path)
    rule.establish_baseline()
    assert (tmp_path / ".architecture" / "baseline" / "renamed-files.txt").exists()


def test_symlinked_repo_root_still_scopes(tmp_path: Path) -> None:
    # A symlinked repo root (e.g. macOS /tmp → /private/tmp) must not break
    # scoping: enumerated paths resolve symlinks, so the root must too, else
    # _repo_relative falls back to the absolute path and every is_in_scope
    # prefix test fails — a silent always-pass. Regression guard.
    real = tmp_path / "real"
    (real / "src").mkdir(parents=True)
    (real / "src" / "bad.py").write_text("BADWORD\n", encoding="utf-8")
    link = tmp_path / "link"
    link.symlink_to(real)
    rule = _BadWord(repo_root=link, roots=("src",))
    assert {str(p) for p in rule.collect_violations()} == {"src/bad.py"}
    assert rule.run() == 1  # the net-new offender is detected through the symlink
