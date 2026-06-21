"""Tests for the CORE check script_help_smoke (v0.6.0)."""

from __future__ import annotations

import sys
from pathlib import Path

from _core_check_assertions import assert_no_repo_identity

from tc_fitness.core_checks.script_help_smoke import (
    ScriptHelpSmoke,
    build,
    extract_declared_flags,
    main,
    script_help_violates,
)

_GOOD_CLI = """
import argparse

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--agent")
    parser.add_argument("--out-dir")
    parser.parse_args()
    return 0

if __name__ == "__main__":
    main()
"""

# Missing flag in help: argparse renders all add_argument flags, so the only
# way --help omits a declared flag is a parser that fails to build. We emulate
# the broken case with a script that crashes before printing help.
_BROKEN_CLI = """
import argparse

raise SystemExit(2)  # work at import time before argparse fires

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--agent")
    return 0
"""

_NOT_A_CLI = """
def helper():
    return 1
"""


def _seed(tmp_path: Path, rel: str, body: str) -> Path:
    p = tmp_path / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body, encoding="utf-8")
    return p


def test_extract_declared_flags() -> None:
    import ast

    tree = ast.parse(_GOOD_CLI)
    assert extract_declared_flags(tree) == ("--agent", "--out-dir")


def test_good_cli_passes(tmp_path: Path) -> None:
    p = _seed(tmp_path, "scripts/good.py", _GOOD_CLI)
    assert script_help_violates(p, python=sys.executable, timeout=10) is False


def test_broken_cli_flagged(tmp_path: Path) -> None:
    p = _seed(tmp_path, "scripts/broken.py", _BROKEN_CLI)
    assert script_help_violates(p, python=sys.executable, timeout=10) is True


def test_non_cli_is_not_in_scope(tmp_path: Path) -> None:
    p = _seed(tmp_path, "scripts/helper.py", _NOT_A_CLI)
    # No main()+ArgumentParser → never a violation regardless of help.
    assert script_help_violates(p, python=sys.executable, timeout=10) is False


def test_rule_scopes_roots_and_skips_tests(tmp_path: Path) -> None:
    _seed(tmp_path, "scripts/broken.py", _BROKEN_CLI)
    _seed(tmp_path, "scripts/tests/test_thing.py", _BROKEN_CLI)  # skip segment
    _seed(tmp_path, "vendor/broken.py", _BROKEN_CLI)  # out of roots
    rule = ScriptHelpSmoke.from_config({"roots": ["scripts"], "help_timeout_seconds": 10}, repo_root=tmp_path)
    assert {str(p) for p in rule.collect_violations()} == {"scripts/broken.py"}


def test_run_then_establish_grandfathers(tmp_path: Path) -> None:
    _seed(tmp_path, "scripts/broken.py", _BROKEN_CLI)
    rule = ScriptHelpSmoke.from_config({"roots": ["scripts"], "help_timeout_seconds": 10}, repo_root=tmp_path)
    assert rule.run() == 1
    rule.establish_baseline()
    assert rule.run() == 0


def test_main_establish_baseline_mode(tmp_path: Path) -> None:
    _seed(tmp_path, "scripts/broken.py", _BROKEN_CLI)
    rc = main(["--establish-baseline", "--repo-root", str(tmp_path)])
    assert rc == 0
    assert (tmp_path / ".architecture" / "baseline" / "script-help-smoke-files.txt").exists()


def test_build_returns_rule() -> None:
    assert isinstance(build({}), ScriptHelpSmoke)


def test_no_repo_strings_in_executable_code() -> None:
    import tc_fitness.core_checks.script_help_smoke as mod

    assert_no_repo_identity(mod.__file__)
