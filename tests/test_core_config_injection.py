"""End-to-end proof: a configured ``core:`` entry scans the configured tree (v0.6.1).

The v0.6.0 CORE checks are repo-agnostic: each reads its ``roots`` / ``extensions``
/ thresholds from the consumer's ``[tool.tc_fitness.core_checks.<module>]`` block,
and with no config its class-attribute defaults (``roots=()``) enumerate zero files
— a vacuous pass. v0.6.1 wires that config through the dispatch path so a bound
CORE check is actually CONSUMABLE.

These tests drive the REAL ``tc-fitness run`` gate surface a consumer's
``uv run tc-fitness run`` invokes: a tmp repo with a ``pyproject.toml`` carrying a
``[tool.tc_fitness]`` catalogue step whose catalogue holds
``check="core:no_duplicate_string"``, plus a
``[tool.tc_fitness.core_checks.no_duplicate_string]`` block (``roots=["src"]`` +
``min_occurrences=3``), and a ``src/dup.py`` with a string literal repeated 3 times.

The proofs:

- the configured CORE check REPORTS the violation (the gate FAILs, the file is
  flagged) — config was injected and the right tree scanned;
- a clean tree PASSes;
- ``--establish-baseline`` writes ``.architecture/baseline/no-duplicate-string-files.txt``
  and the subsequent gate run PASSes (the offender is grandfathered);
- a ``core:`` entry dispatches IN-PROCESS even under ``dispatch = "subprocess"``
  (never the non-existent ``tc_fitness/core_checks/<module>.py`` script path);
- a CORE entry with NO config block stays vacuous (the pre-v0.6.1 behaviour —
  proves the injection is the ONLY thing that makes it bite).
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

from tc_fitness.gate import main, run_gate
from tc_fitness.gate_config import load_config

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")

# A module with one string literal (>= 10 chars) repeated 3 times — a Sonar-S1192
# duplicate. The literal is non-docstring, non-blank, so the CORE detector counts
# all three occurrences and flags the file at the default min_occurrences=3.
_DUP_BODY = '''"""Fixture module with a duplicated literal."""


def a() -> None:
    raise ValueError("the very same long message")


def b() -> None:
    raise ValueError("the very same long message")


def c() -> None:
    raise ValueError("the very same long message")
'''

_CLEAN_BODY = '''"""Fixture module with the literal extracted to a constant."""

_MSG = "the very same long message"


def a() -> None:
    raise ValueError(_MSG)


def b() -> None:
    raise ValueError(_MSG)


def c() -> None:
    raise ValueError(_MSG)
'''

# A real catalogue module binding the engine CORE check via the ``core:`` namespace
# — exactly the row a consumer adds (per the core_checks/__init__ docstring).
_CATALOGUE_SRC = (
    "from tc_fitness.catalogue import RuleEntry\n"
    "ALL_ENTRIES = (\n"
    "    RuleEntry(\n"
    "        id='no-duplicate-string',\n"
    "        gate='no-duplicate-string',\n"
    "        check='core:no_duplicate_string',\n"
    "        category='maintainability',\n"
    "        summary='No string literal duplicated 3+ times in one module.',\n"
    "    ),\n"
    ")\n"
)


def _plain(text: str) -> str:
    return _ANSI_RE.sub("", text)


def _scaffold(repo: Path, *, src_body: str, core_block: str, dispatch: str = "inprocess") -> None:
    """Lay down a consumer repo: catalogue module + pyproject gate + a src tree.

    ``core_block`` is the literal ``[tool.tc_fitness.core_checks.no_duplicate_string]``
    body (may be empty to prove the no-config vacuous-pass path). ``dispatch``
    sets the catalogue step's dispatch mode.
    """
    checks = repo / "scripts" / "checks"
    checks.mkdir(parents=True)
    (repo / "scripts" / "__init__.py").write_text("")
    (checks / "__init__.py").write_text("")
    (checks / "synthetic_core_cat.py").write_text(_CATALOGUE_SRC)

    src = repo / "src"
    src.mkdir(parents=True)
    (src / "dup.py").write_text(src_body, encoding="utf-8")

    pyproject = (
        "[project]\n"
        "name = 'consumer-demo'\n"
        "version = '0.0.0'\n"
        "\n"
        "[tool.tc_fitness]\n"
        'name = "consumer gate"\n'
        "\n"
        "[[tool.tc_fitness.steps]]\n"
        'id = "fitness"\n'
        'summary = "architecture fitness functions"\n'
        'catalogue = "scripts.checks.synthetic_core_cat:ALL_ENTRIES"\n'
        'checks_dir = "scripts/checks"\n'
        f'dispatch = "{dispatch}"\n'
    )
    if core_block:
        pyproject += "\n" + core_block
    (repo / "pyproject.toml").write_text(pyproject)


_CORE_BLOCK = (
    "[tool.tc_fitness.core_checks.no_duplicate_string]\n"
    'roots = ["src"]\n'
    'extensions = [".py"]\n'
    "min_length = 10\n"
    "min_occurrences = 3\n"
)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    return tmp_path


@pytest.fixture(autouse=True)
def _clean_sys_modules() -> object:
    """Drop the synthetic catalogue module so each test re-imports a fresh one."""
    before = set(sys.modules)
    yield
    for name in set(sys.modules) - before:
        if "synthetic_core_cat" in name or name.startswith("scripts"):
            del sys.modules[name]


def _baseline_file(repo: Path) -> Path:
    return repo / ".architecture" / "baseline" / "no-duplicate-string-files.txt"


# --------------------------------------------------------------------------- #
# The headline proof: a configured core: check flags a real duplicate.
# --------------------------------------------------------------------------- #


def test_configured_core_check_flags_real_duplicate(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _scaffold(repo, src_body=_DUP_BODY, core_block=_CORE_BLOCK)

    outcome = run_gate(load_config(repo), repo)
    out = _plain(capsys.readouterr().out)

    # Config was injected (roots=["src"]) → the real tree was scanned → the
    # duplicate in src/dup.py is reported and the gate FAILs.
    assert not outcome.ok, "a configured core: check must gate on a real duplicate"
    assert "FAIL [no-duplicate-string]" in out
    assert "dup.py" in out  # the offending file is named in the rule's emit


def test_configured_core_check_passes_on_clean_tree(repo: Path) -> None:
    _scaffold(repo, src_body=_CLEAN_BODY, core_block=_CORE_BLOCK)
    assert run_gate(load_config(repo), repo).ok


def test_core_check_with_no_config_is_vacuous(repo: Path) -> None:
    # No [tool.tc_fitness.core_checks.*] block → roots=() → zero files enumerated
    # → vacuous pass even with the duplicate present. This is the pre-v0.6.1
    # behaviour and proves the config injection is the ONLY thing that makes the
    # CORE check bite.
    _scaffold(repo, src_body=_DUP_BODY, core_block="")
    assert run_gate(load_config(repo), repo).ok


# --------------------------------------------------------------------------- #
# core: always dispatches in-process — even under dispatch = "subprocess".
# --------------------------------------------------------------------------- #


def test_core_check_dispatches_in_process_under_subprocess_mode(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # A consumer whose catalogue step is dispatch="subprocess" (taz's mode) must
    # STILL run a core: entry in-process — never look for the non-existent
    # tc_fitness/core_checks/<module>.py under scripts/checks.
    _scaffold(repo, src_body=_DUP_BODY, core_block=_CORE_BLOCK, dispatch="subprocess")

    outcome = run_gate(load_config(repo), repo)
    out = _plain(capsys.readouterr().out)

    assert not outcome.ok
    assert "FAIL [no-duplicate-string]" in out
    assert "check script not found" not in out  # the broken subprocess path is never taken


# --------------------------------------------------------------------------- #
# --establish-baseline writes the baseline; the subsequent run passes.
# --------------------------------------------------------------------------- #


def test_establish_baseline_then_run_passes(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _scaffold(repo, src_body=_DUP_BODY, core_block=_CORE_BLOCK)

    # 1) Establish mode through the REAL console entrypoint.
    rc_establish = main(["run", "--repo-root", str(repo), "--establish-baseline"])
    establish_out = _plain(capsys.readouterr().out)
    assert rc_establish == 0, "establish mode exits 0 (it freezes, it never gates)"

    baseline = _baseline_file(repo)
    assert baseline.exists(), "establish mode must write the per-file baseline"
    body = baseline.read_text(encoding="utf-8")
    assert "src/dup.py" in body, "the offender is frozen into the baseline"
    assert "established baseline:" in establish_out

    # 2) A normal run now PASSes — the offender is grandfathered, nothing net-new.
    rc_run = main(["run", "--repo-root", str(repo)])
    assert rc_run == 0, "after establishing, the run gates only on NET-NEW offenders"


def test_net_new_offender_fails_after_baseline(repo: Path) -> None:
    # Freeze src/dup.py as the baseline, then add a SECOND duplicate file: the
    # grandfathered file is tolerated but the net-new one FAILs — proving the
    # baseline path is wired through the configured-roots scan.
    _scaffold(repo, src_body=_DUP_BODY, core_block=_CORE_BLOCK)
    assert main(["run", "--repo-root", str(repo), "--establish-baseline"]) == 0

    (repo / "src" / "dup2.py").write_text(_DUP_BODY, encoding="utf-8")
    assert main(["run", "--repo-root", str(repo)]) == 1, "a net-new offender must gate"
