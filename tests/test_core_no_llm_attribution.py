"""Tests for the CORE check no_llm_attribution (Autonomous Delivery Platform SP-A / SGO-156).

The check has two surfaces that share ONE detector (:func:`scan_text`):
* a :class:`FitnessRule` that scans in-repo files for LLM-attribution residue, and
* the standalone ``scan_text`` helper reused by the commit-msg strip hook (SGO-159)
  and the CI trailer-reject leg (SGO-160) to scan commit messages + PR title/body.
"""

from __future__ import annotations

from pathlib import Path

from tc_fitness.core_checks.no_llm_attribution import (
    NoLlmAttribution,
    build,
    main,
    scan_text,
)

ROBOT = "\U0001f916"  # 🤖


def _seed(tmp_path: Path, rel: str, body: str) -> Path:
    p = tmp_path / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body, encoding="utf-8")
    return p


# ── scan_text: the shared detector (hook + CI legs + file scan all key on it) ──


def test_scan_text_flags_coauthor_claude_trailer() -> None:
    hits = scan_text("feat: x\n\nCo-Authored-By: Claude <noreply@anthropic.com>")
    assert hits, "the Co-Authored-By: Claude trailer must be flagged"


def test_scan_text_flags_generated_with_claude_code() -> None:
    hits = scan_text(f"{ROBOT} Generated with [Claude Code](https://claude.com/claude-code)")
    assert hits


def test_scan_text_flags_anthropic_noreply_email() -> None:
    assert scan_text("Signed-off-by: bot <noreply@anthropic.com>")


def test_scan_text_flags_bare_robot_emoji() -> None:
    assert scan_text(f"nice work {ROBOT}")


def test_scan_text_is_provider_generic() -> None:
    # Cursor / Copilot co-author trailers are the same class of residue.
    assert scan_text("Co-authored-by: Cursor Agent <cursor@cursor.com>")


def test_scan_text_clean_text_passes() -> None:
    # A bare mention of the word "Anthropic" and a genuine HUMAN co-author must NOT flag.
    clean = (
        "This module talks to the Anthropic API.\n\n"
        "Co-Authored-By: Jane Doe <jane@example.com>\n"
        "Reviewed-by: Sam <sam@example.com>"
    )
    assert scan_text(clean) == []


def test_scan_text_reports_signature_names() -> None:
    hits = scan_text("Co-Authored-By: Claude <noreply@anthropic.com>")
    sigs = {h.signature for h in hits}
    assert "attribution_trailer" in sigs
    assert "anthropic_noreply" in sigs


# ── FitnessRule surface: file scan, baseline grandfathering (guard-forward) ──


def test_file_has_violation_true_and_false(tmp_path: Path) -> None:
    rule = build({"roots": ["."], "extensions": [".py", ".md"]}, repo_root=tmp_path)
    dirty = _seed(tmp_path, "src/a.py", f"# {ROBOT} Generated with Claude Code\nx = 1\n")
    clean = _seed(tmp_path, "src/b.py", "x = 1  # ordinary code\n")
    assert rule.file_has_violation(dirty) is True
    assert rule.file_has_violation(clean) is False


def test_functional_claude_string_is_not_authorship(tmp_path: Path) -> None:
    # A functional in-source string that merely names the tool (no attribution
    # signature) must NOT be flagged — only attribution residue is.
    rule = build({"roots": ["."], "extensions": [".py"]}, repo_root=tmp_path)
    p = _seed(tmp_path, "src/c.py", 'PREFIX = "Claude Code sub-agent worktrees"\n')
    assert rule.file_has_violation(p) is False


def test_run_fails_then_establish_grandfathers(tmp_path: Path) -> None:
    _seed(tmp_path, "src/a.py", f"# {ROBOT} Generated with Claude Code\n")
    rule = NoLlmAttribution.from_config({"roots": ["src"], "extensions": [".py"]}, repo_root=tmp_path)
    assert rule.run() == 1
    rule.establish_baseline()
    assert rule.run() == 0


def test_main_establish_baseline_mode(tmp_path: Path) -> None:
    _seed(tmp_path, "src/a.py", "Co-Authored-By: Claude <noreply@anthropic.com>\n")
    rc = main(["--establish-baseline", "--repo-root", str(tmp_path)])
    assert rc == 0
    assert (tmp_path / ".architecture" / "baseline" / "no-llm-attribution-files.txt").exists()
