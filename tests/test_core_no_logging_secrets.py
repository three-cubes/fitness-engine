"""Tests for the CORE check no_logging_secrets (v0.6.0 security-freshness batch)."""

from __future__ import annotations

import re
from pathlib import Path

from tc_fitness.core_checks.no_logging_secrets import (
    DEFAULT_DIRECT_SINKS,
    DEFAULT_LOG_METHODS,
    DEFAULT_SECRET_PATTERNS,
    NoLoggingSecrets,
    build,
    main,
    module_logs_secret,
)

_PATTERNS = tuple(re.compile(p) for p in DEFAULT_SECRET_PATTERNS)


def _seed(tmp_path: Path, rel: str, body: str) -> Path:
    p = tmp_path / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body, encoding="utf-8")
    return p


def test_detection_flags_logged_secret(tmp_path: Path) -> None:
    p = _seed(tmp_path, "m.py", "import logging\nlogging.info(api_key)\n")
    assert (
        module_logs_secret(
            p, patterns=_PATTERNS, log_methods=DEFAULT_LOG_METHODS, direct_sinks=DEFAULT_DIRECT_SINKS
        )
        is True
    )


def test_detection_flags_fstring_interpolation(tmp_path: Path) -> None:
    p = _seed(tmp_path, "m.py", 'logger.info(f"auth = {access_token}")\n')
    assert (
        module_logs_secret(
            p, patterns=_PATTERNS, log_methods=DEFAULT_LOG_METHODS, direct_sinks=DEFAULT_DIRECT_SINKS
        )
        is True
    )


def test_detection_flags_raise_with_secret(tmp_path: Path) -> None:
    p = _seed(tmp_path, "m.py", 'raise RuntimeError(f"bad token: {token}")\n')
    assert (
        module_logs_secret(
            p, patterns=_PATTERNS, log_methods=DEFAULT_LOG_METHODS, direct_sinks=DEFAULT_DIRECT_SINKS
        )
        is True
    )


def test_redacted_summary_is_clean(tmp_path: Path) -> None:
    p = _seed(tmp_path, "m.py", 'logger.info("api_key present: %s", api_key is not None)\n')
    assert (
        module_logs_secret(
            p, patterns=_PATTERNS, log_methods=DEFAULT_LOG_METHODS, direct_sinks=DEFAULT_DIRECT_SINKS
        )
        is False
    )


def test_non_secret_name_is_clean(tmp_path: Path) -> None:
    p = _seed(tmp_path, "m.py", "logger.info(client_id)\n")
    assert (
        module_logs_secret(
            p, patterns=_PATTERNS, log_methods=DEFAULT_LOG_METHODS, direct_sinks=DEFAULT_DIRECT_SINKS
        )
        is False
    )


def test_exempt_file_skipped_via_config(tmp_path: Path) -> None:
    _seed(tmp_path, "src/boundary.py", "logging.info(api_key)\n")
    rule = build({"roots": ["src"], "exempt_files": ["src/boundary.py"]}, repo_root=tmp_path)
    assert rule.collect_violations() == set()


def test_run_fails_then_establish_grandfathers(tmp_path: Path) -> None:
    _seed(tmp_path, "src/leak.py", "logging.info(api_key)\n")
    rule = NoLoggingSecrets.from_config({"roots": ["src"]}, repo_root=tmp_path)
    assert rule.run() == 1
    rule.establish_baseline()
    assert rule.run() == 0


def test_main_establish_baseline_mode(tmp_path: Path) -> None:
    _seed(tmp_path, "leak.py", "logging.info(api_key)\n")
    rc = main(["--establish-baseline", "--repo-root", str(tmp_path)])
    assert rc == 0
    assert (tmp_path / ".architecture" / "baseline" / "no-logging-secrets-files.txt").exists()
