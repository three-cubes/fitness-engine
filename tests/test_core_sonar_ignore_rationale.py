"""Tests for the CORE check sonar_ignore_rationale (v0.6.0)."""

from __future__ import annotations

import ast
from pathlib import Path

from tc_fitness.core_checks.sonar_ignore_rationale import (
    DEFAULT_RULE_KEY_PATTERN,
    SonarIgnoreRationale,
    build,
    file_has_unjustified_ignore,
    main,
)

_JUSTIFIED = """\
# python:S5547 - HMAC-SHA1 used only for legacy fingerprinting, never for security.
sonar.issue.ignore.multicriteria.e1.ruleKey=python:S5547
sonar.issue.ignore.multicriteria.e1.resourceKey=app/fingerprint.py
"""

_BARE = """\
# TODO
sonar.issue.ignore.multicriteria.e1.ruleKey=python:S5547
"""


def _seed(tmp_path: Path, rel: str, body: str) -> Path:
    p = tmp_path / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body, encoding="utf-8")
    return p


def test_detection_core_flags_bare_ignore(tmp_path: Path) -> None:
    p = _seed(tmp_path, "sonar-project.properties", _BARE)
    assert file_has_unjustified_ignore(p, rule_key_pattern=DEFAULT_RULE_KEY_PATTERN) is True


def test_justified_ignore_clean(tmp_path: Path) -> None:
    p = _seed(tmp_path, "sonar-project.properties", _JUSTIFIED)
    assert file_has_unjustified_ignore(p, rule_key_pattern=DEFAULT_RULE_KEY_PATTERN) is False


def test_absent_file_is_clean(tmp_path: Path) -> None:
    rule = SonarIgnoreRationale.from_config({}, repo_root=tmp_path)
    assert rule.collect_violations() == set()
    assert rule.run() == 0


def test_enumerates_only_the_sonar_file(tmp_path: Path) -> None:
    _seed(tmp_path, "sonar-project.properties", _BARE)
    rule = SonarIgnoreRationale.from_config({}, repo_root=tmp_path)
    assert {str(p) for p in rule.collect_violations()} == {"sonar-project.properties"}


def test_sonar_file_name_config_driven(tmp_path: Path) -> None:
    _seed(tmp_path, "config/sonar.props", _BARE)
    rule = build({"sonar_file": "config/sonar.props"}, repo_root=tmp_path)
    assert {str(p) for p in rule.collect_violations()} == {"config/sonar.props"}


def test_run_then_establish_grandfathers(tmp_path: Path) -> None:
    _seed(tmp_path, "sonar-project.properties", _BARE)
    rule = SonarIgnoreRationale.from_config({}, repo_root=tmp_path)
    assert rule.run() == 1
    rule.establish_baseline()
    assert rule.run() == 0


def test_main_establish_baseline_mode(tmp_path: Path) -> None:
    _seed(tmp_path, "sonar-project.properties", _BARE)
    rc = main(["--establish-baseline", "--repo-root", str(tmp_path)])
    assert rc == 0
    assert (tmp_path / ".architecture" / "baseline" / "sonar-ignore-rationale-files.txt").exists()


def test_no_repo_strings_in_executable_code() -> None:
    import tc_fitness.core_checks.sonar_ignore_rationale as mod

    text = Path(mod.__file__).read_text(encoding="utf-8")
    tree = ast.parse(text)
    docstring_ids: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Module | ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            doc = ast.get_docstring(node, clean=False)
            if doc is not None and node.body:
                first = node.body[0]
                if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant):
                    docstring_ids.add(id(first.value))
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docstring_ids:
            lowered = node.value.lower()
            for tok in ("kairix", "tc-agent-zone", "agent-zone", "kata"):
                assert tok not in lowered, f"repo identity leaked: {tok}"
