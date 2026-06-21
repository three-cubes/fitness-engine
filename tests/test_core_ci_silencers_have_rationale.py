"""Tests for the CORE check ci_silencers_have_rationale (v0.6.0)."""

from __future__ import annotations

import ast
import re
from pathlib import Path

from tc_fitness.core_checks.ci_silencers_have_rationale import (
    DEFAULT_RATIONALE_TOKENS,
    DEFAULT_SILENCER_PATTERNS,
    DEFAULT_WINDOW,
    CiSilencersHaveRationale,
    build,
    file_has_unjustified_silencer,
    main,
)

_BARE = """\
jobs:
  lint:
    continue-on-error: true
    steps:
      - run: ruff check .
"""

_COMMENT_REASON = """\
jobs:
  lint:
    continue-on-error: true  # non-blocking: advisory lint job
    steps:
      - run: ruff check .
"""

_NEARBY_REASON = """\
jobs:
  lint:
    # intentional: this gate is advisory only
    continue-on-error: true
    steps:
      - run: ruff check .
"""

_SIL = re.compile("|".join(f"(?:{p})" for p in DEFAULT_SILENCER_PATTERNS))
_RAT = re.compile("|".join(re.escape(t) for t in DEFAULT_RATIONALE_TOKENS), re.IGNORECASE)


def _seed(tmp_path: Path, rel: str, body: str) -> Path:
    p = tmp_path / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body, encoding="utf-8")
    return p


def _detect(p: Path) -> bool:
    return file_has_unjustified_silencer(p, silencer_re=_SIL, rationale_re=_RAT, window=DEFAULT_WINDOW)


def test_detection_core_flags_bare(tmp_path: Path) -> None:
    p = _seed(tmp_path, "ci.yml", _BARE)
    assert _detect(p) is True


def test_trailing_comment_satisfies(tmp_path: Path) -> None:
    p = _seed(tmp_path, "ci.yml", _COMMENT_REASON)
    assert _detect(p) is False


def test_nearby_token_satisfies(tmp_path: Path) -> None:
    p = _seed(tmp_path, "ci.yml", _NEARBY_REASON)
    assert _detect(p) is False


def test_enumerates_workflow_dir(tmp_path: Path) -> None:
    _seed(tmp_path, ".github/workflows/ci.yml", _BARE)
    _seed(tmp_path, ".github/workflows/ok.yml", _COMMENT_REASON)
    rule = CiSilencersHaveRationale.from_config({}, repo_root=tmp_path)
    assert {str(p) for p in rule.collect_violations()} == {".github/workflows/ci.yml"}


def test_scan_files_config_driven(tmp_path: Path) -> None:
    _seed(tmp_path, "scripts/check.sh", "pytest || true\n")
    rule = build({"scan_files": ["scripts/check.sh"]}, repo_root=tmp_path)
    assert {str(p) for p in rule.collect_violations()} == {"scripts/check.sh"}


def test_run_then_establish_grandfathers(tmp_path: Path) -> None:
    _seed(tmp_path, ".github/workflows/ci.yml", _BARE)
    rule = CiSilencersHaveRationale.from_config({}, repo_root=tmp_path)
    assert rule.run() == 1
    rule.establish_baseline()
    assert rule.run() == 0


def test_main_establish_baseline_mode(tmp_path: Path) -> None:
    _seed(tmp_path, ".github/workflows/ci.yml", _BARE)
    rc = main(["--establish-baseline", "--repo-root", str(tmp_path)])
    assert rc == 0
    assert (tmp_path / ".architecture" / "baseline" / "ci-silencers-have-rationale-files.txt").exists()


def test_no_repo_strings_in_executable_code() -> None:
    import tc_fitness.core_checks.ci_silencers_have_rationale as mod

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
