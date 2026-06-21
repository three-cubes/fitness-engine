"""Tests for the CORE check no_internal_patches_ts (v0.6.0 security-freshness batch)."""

from __future__ import annotations

from pathlib import Path

from tc_fitness.core_checks.no_internal_patches_ts import (
    NoInternalPatchesTs,
    build,
    file_mocks_internal_ts,
    main,
)

_INTERNAL = frozenset({"mcp-x", "mcp-kairix"})
_EXEMPT_EXACT = frozenset({"fs", "axios", "console"})
_EXEMPT_PREFIXES = ("node:", "@azure/")


def _seed(tmp_path: Path, rel: str, body: str) -> Path:
    p = tmp_path / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body, encoding="utf-8")
    return p


def _flags(p: Path) -> bool:
    return file_mocks_internal_ts(
        p, internal_packages=_INTERNAL, exempt_exact=_EXEMPT_EXACT, exempt_prefixes=_EXEMPT_PREFIXES
    )


def test_flags_relative_mock(tmp_path: Path) -> None:
    p = _seed(tmp_path, "a.test.ts", "vi.mock('../../src/client.js', () => ({}));\n")
    assert _flags(p) is True


def test_flags_internal_workspace_package_mock(tmp_path: Path) -> None:
    p = _seed(tmp_path, "a.test.ts", "jest.mock('mcp-kairix/dist/x.js');\n")
    assert _flags(p) is True


def test_external_mock_is_clean(tmp_path: Path) -> None:
    p = _seed(tmp_path, "a.test.ts", "vi.mock('node:fs/promises');\nvi.mock('axios');\n")
    assert _flags(p) is False


def test_spyon_internal_namespace_flagged(tmp_path: Path) -> None:
    body = "import * as client from '../src/client.js';\nvi.spyOn(client, 'graphGet');\n"
    p = _seed(tmp_path, "a.test.ts", body)
    assert _flags(p) is True


def test_spyon_console_is_clean(tmp_path: Path) -> None:
    p = _seed(tmp_path, "a.test.ts", "vi.spyOn(console, 'log');\n")
    assert _flags(p) is False


def test_mock_in_comment_is_clean(tmp_path: Path) -> None:
    p = _seed(tmp_path, "a.test.ts", "// vi.mock('../src/x.js')\n/* vi.mock('../src/y.js') */\n")
    assert _flags(p) is False


def test_run_fails_then_establish_grandfathers(tmp_path: Path) -> None:
    _seed(tmp_path, "pkg/a.test.ts", "vi.mock('../../src/client.js');\n")
    rule = NoInternalPatchesTs.from_config(
        {"roots": ["pkg"], "internal_packages": ["mcp-x"], "exempt_specifiers": ["fs"]}, repo_root=tmp_path
    )
    assert rule.run() == 1
    rule.establish_baseline()
    assert rule.run() == 0


def test_main_establish_baseline_mode(tmp_path: Path) -> None:
    _seed(tmp_path, "a.test.ts", "vi.mock('../../src/client.js');\n")
    rc = main(["--establish-baseline", "--repo-root", str(tmp_path)])
    assert rc == 0
    assert (tmp_path / ".architecture" / "baseline" / "no-internal-patches-ts-files.txt").exists()


def test_non_test_ts_out_of_scope(tmp_path: Path) -> None:
    rule = build({"roots": ["."], "internal_packages": ["mcp-x"]}, repo_root=tmp_path)
    _seed(tmp_path, "src/x.ts", "vi.mock('../../src/client.js');\n")
    # .ts (not .test.ts) is out of scope via the default extensions.
    assert rule.collect_violations() == set()
