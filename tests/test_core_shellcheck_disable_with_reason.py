"""Tests for the CORE check shellcheck_disable_with_reason (v0.6.0)."""

from __future__ import annotations

import ast
from pathlib import Path

from tc_fitness.core_checks.shellcheck_disable_with_reason import (
    DEFAULT_MIN_RATIONALE_LEN,
    DEFAULT_RATIONALE_MARKERS,
    ShellcheckDisableWithReason,
    build,
    file_has_unjustified_disable,
    is_shell_file,
    main,
)

_BARE = """\
#!/usr/bin/env bash
# shellcheck disable=SC1090
. "$SECRETS_FILE"
"""

_INLINE_REASON = """\
#!/usr/bin/env bash
# shellcheck disable=SC2034  # exported via process substitution below
x=1
"""

_PRECEDING_REASON = """\
#!/usr/bin/env bash
# safe -- sourced path computed from a controlled config var
# shellcheck disable=SC1090
. "$SECRETS_FILE"
"""


def _seed(tmp_path: Path, rel: str, body: str) -> Path:
    p = tmp_path / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body, encoding="utf-8")
    return p


def _kw() -> dict:
    return {"markers": DEFAULT_RATIONALE_MARKERS, "min_len": DEFAULT_MIN_RATIONALE_LEN}


def test_detection_core_flags_bare(tmp_path: Path) -> None:
    p = _seed(tmp_path, "x.sh", _BARE)
    assert file_has_unjustified_disable(p, **_kw()) is True


def test_inline_reason_satisfies(tmp_path: Path) -> None:
    p = _seed(tmp_path, "x.sh", _INLINE_REASON)
    assert file_has_unjustified_disable(p, **_kw()) is False


def test_preceding_reason_satisfies(tmp_path: Path) -> None:
    p = _seed(tmp_path, "x.sh", _PRECEDING_REASON)
    assert file_has_unjustified_disable(p, **_kw()) is False


def test_shebang_file_without_sh_ext_detected(tmp_path: Path) -> None:
    p = _seed(tmp_path, "src/deploy", _BARE)
    assert is_shell_file(p) is True
    rule = ShellcheckDisableWithReason.from_config({"roots": ["src"]}, repo_root=tmp_path)
    assert {str(x) for x in rule.collect_violations()} == {"src/deploy"}


def test_rule_from_config_scopes_roots(tmp_path: Path) -> None:
    _seed(tmp_path, "src/a.sh", _BARE)
    _seed(tmp_path, "vendor/a.sh", _BARE)
    rule = ShellcheckDisableWithReason.from_config({"roots": ["src"]}, repo_root=tmp_path)
    assert {str(p) for p in rule.collect_violations()} == {"src/a.sh"}


def test_run_then_establish_grandfathers(tmp_path: Path) -> None:
    _seed(tmp_path, "src/a.sh", _BARE)
    rule = build({"roots": ["src"]}, repo_root=tmp_path)
    assert rule.run() == 1
    rule.establish_baseline()
    assert rule.run() == 0


def test_main_establish_baseline_mode(tmp_path: Path) -> None:
    _seed(tmp_path, "a.sh", _BARE)
    rc = main(["--establish-baseline", "--repo-root", str(tmp_path)])
    assert rc == 0
    assert (tmp_path / ".architecture" / "baseline" / "shellcheck-disable-with-reason-files.txt").exists()


def test_no_repo_strings_in_executable_code() -> None:
    import tc_fitness.core_checks.shellcheck_disable_with_reason as mod

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
