"""Tests for the CORE check no_noop_test_scripts (v0.6.0)."""

from __future__ import annotations

import json
import re
from pathlib import Path

from _core_check_assertions import assert_no_repo_identity

from tc_fitness.core_checks.no_noop_test_scripts import (
    NoNoopTestScripts,
    build,
    main,
    script_is_noop,
)

_PLACEHOLDER = re.compile(
    r"(?:no tests? yet|todo|placeholder|not implemented|skip tests?|exit\s+0)", re.IGNORECASE
)
_REAL = re.compile(r"\b(vitest|jest|node\s+--test|tsx|mocha|tap|ava|playwright)\b")


def _seed_pkg(tmp_path: Path, rel: str, test_script: str) -> Path:
    p = tmp_path / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"scripts": {"test": test_script}}), encoding="utf-8")
    return p


def test_placeholder_is_noop() -> None:
    assert (
        script_is_noop("echo 'no tests yet' && exit 0", placeholder=_PLACEHOLDER, real_runner=_REAL) is True
    )


def test_real_runner_is_not_noop() -> None:
    assert script_is_noop("vitest run src --coverage", placeholder=_PLACEHOLDER, real_runner=_REAL) is False


def test_placeholder_with_real_runner_passes() -> None:
    # mentions "exit 0" but also runs vitest → real
    assert script_is_noop("vitest run || exit 0", placeholder=_PLACEHOLDER, real_runner=_REAL) is False


def test_prod_prefix_scoping(tmp_path: Path) -> None:
    _seed_pkg(tmp_path, "agentic/pkg/package.json", "echo 'no tests yet'")
    _seed_pkg(tmp_path, "vendor/pkg/package.json", "echo 'no tests yet'")
    rule = NoNoopTestScripts.from_config({"prod_package_prefixes": ["agentic/"]}, repo_root=tmp_path)
    assert {str(p) for p in rule.collect_violations()} == {"agentic/pkg/package.json"}


def test_root_manifest_in_scope(tmp_path: Path) -> None:
    _seed_pkg(tmp_path, "package.json", "todo")
    rule = NoNoopTestScripts.from_config({}, repo_root=tmp_path)
    assert {str(p) for p in rule.collect_violations()} == {"package.json"}


def test_skip_parts_are_config_driven(tmp_path: Path) -> None:
    _seed_pkg(tmp_path, "agentic/pkg/node_modules/dep/package.json", "todo")
    _seed_pkg(tmp_path, "agentic/pkg/package.json", "todo")
    rule = NoNoopTestScripts.from_config({"prod_package_prefixes": ["agentic/"]}, repo_root=tmp_path)
    assert {str(p) for p in rule.collect_violations()} == {"agentic/pkg/package.json"}


def test_run_then_establish_grandfathers(tmp_path: Path) -> None:
    _seed_pkg(tmp_path, "agentic/pkg/package.json", "echo 'no tests yet'")
    rule = NoNoopTestScripts.from_config({"prod_package_prefixes": ["agentic/"]}, repo_root=tmp_path)
    assert rule.run() == 1
    rule.establish_baseline()
    assert rule.run() == 0


def test_main_establish_baseline_mode(tmp_path: Path) -> None:
    _seed_pkg(tmp_path, "package.json", "todo")
    rc = main(["--establish-baseline", "--repo-root", str(tmp_path)])
    assert rc == 0
    assert (tmp_path / ".architecture" / "baseline" / "no-noop-test-scripts-files.txt").exists()


def test_build_returns_rule() -> None:
    assert isinstance(build({}), NoNoopTestScripts)


def test_no_repo_strings_in_executable_code() -> None:
    import tc_fitness.core_checks.no_noop_test_scripts as mod

    assert_no_repo_identity(mod.__file__)
