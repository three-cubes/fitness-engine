"""Tests pinning the three reconciled drift-zone decisions.

Drift 1 — override min-length: ONE constant, 40 (strictly-less-than is vague).
Drift 2 — suppression grammar: superset; NOSONAR present in both pattern sets.
Drift 3 — override-marker syntax: em-dash AND hyphen both accepted.

Each test fails if a future edit re-introduces the drift it pins.
"""

from __future__ import annotations

import re

from tc_fitness.ratchet import (
    BARE_SUPPRESSION_PATTERNS,
    COVERAGE_OVERRIDE_RE,
    MUTATION_OVERRIDE_RE,
    OVERRIDE_MIN_REASON_LEN,
    SUPPRESSION_PATTERNS,
    Override,
    contains_suppression,
    is_bare_suppression,
    is_vague_reason,
    make_override_re,
    parse_overrides,
)

# --------------------------------------------------------------------------- #
# Drift zone 1 — ONE override-rationale min-length (40, strictly-less-than)
# --------------------------------------------------------------------------- #


def test_override_min_len_is_forty() -> None:
    # The reconciled constant — NOT 20. Coverage's old 20 was the latent bug.
    assert OVERRIDE_MIN_REASON_LEN == 40


def test_reason_just_under_forty_is_vague() -> None:
    reason = "x" * 39
    assert len(reason) == 39
    assert is_vague_reason(reason) is True


def test_reason_at_forty_is_not_vague() -> None:
    # Strictly-less-than: exactly 40 chars passes (40 < 40 is False).
    reason = "x" * 40
    assert len(reason) == 40
    assert is_vague_reason(reason) is False


def test_reason_above_forty_is_not_vague() -> None:
    reason = "this rationale is comfortably longer than forty characters total"
    assert len(reason) >= 40
    assert is_vague_reason(reason) is False


def test_vague_lead_in_tokens_rejected_even_when_long() -> None:
    # A long-enough string that starts with a vague token is still vague.
    assert is_vague_reason("WIP " + "padding " * 10) is True
    assert is_vague_reason("will-fix-later " + "padding " * 10) is True


def test_trailing_dot_and_whitespace_stripped_before_measuring() -> None:
    # 39 substantive chars + a dot + spaces is still vague (the dot/space
    # don't count toward the length).
    reason = "  " + ("y" * 39) + ".  "
    assert is_vague_reason(reason) is True


# --------------------------------------------------------------------------- #
# min_len floor override (additive) — taz shell directives use 10
# --------------------------------------------------------------------------- #


def test_is_vague_reason_default_min_len_is_unchanged() -> None:
    # v0.1.0 contract: with min_len omitted the floor is OVERRIDE_MIN_REASON_LEN
    # (=40). A 39-char reason is vague, a 40-char reason is not — byte-identical
    # to the default-arg behaviour the existing tests above already pin.
    assert is_vague_reason("x" * 39) is True
    assert is_vague_reason("x" * 40) is False


def test_is_vague_reason_floor_override_to_ten() -> None:
    # taz passes min_len=10: a 10-char reason that was vague at the 40-floor is
    # now acceptable, while a 9-char reason is still vague.
    ten = "x" * 10
    nine = "x" * 9
    assert is_vague_reason(ten) is True  # vague under the default 40-floor
    assert is_vague_reason(ten, min_len=10) is False  # not vague under taz's 10-floor
    assert is_vague_reason(nine, min_len=10) is True  # 9 < 10 still vague


def test_min_len_override_still_strips_dot_and_whitespace() -> None:
    # 9 substantive chars + dot + spaces is still 9 → vague at min_len=10.
    assert is_vague_reason("  " + ("y" * 9) + ".  ", min_len=10) is True
    assert is_vague_reason("  " + ("y" * 10) + ".  ", min_len=10) is False


def test_min_len_override_does_not_bypass_vague_lead_in_tokens() -> None:
    # The vague lead-in set (WIP/TODO/...) is independent of the length floor:
    # a long-enough reason that starts with a vague token is still vague even
    # when min_len is lowered.
    assert is_vague_reason("WIP and then some more words here", min_len=10) is True


def test_constant_unchanged_at_forty() -> None:
    # The constant stays 40 — taz lowers the floor per-call, it does NOT mutate
    # the shared default that kairix's @v0.1.0 gates depend on.
    assert OVERRIDE_MIN_REASON_LEN == 40


def test_parse_overrides_default_min_len_unchanged() -> None:
    # v0.1.0 call shape (no min_len): a short reason is still marked vague at 40.
    line = "coverage-ratchet-acknowledged: scripts/x.py — short reason here"
    overrides = parse_overrides(line, COVERAGE_OVERRIDE_RE)
    assert len(overrides) == 1
    assert overrides[0].vague is True  # "short reason here" is < 40 chars


def test_parse_overrides_forwards_min_len_to_vague_check() -> None:
    # With min_len=10 the same short reason clears the floor → not vague.
    line = "coverage-ratchet-acknowledged: scripts/x.py — short reason here"
    overrides = parse_overrides(line, COVERAGE_OVERRIDE_RE, min_len=10)
    assert len(overrides) == 1
    assert overrides[0].vague is False
    # A still-too-short reason (< 10) remains vague even at the lowered floor.
    short = parse_overrides(
        "coverage-ratchet-acknowledged: scripts/x.py — tiny", COVERAGE_OVERRIDE_RE, min_len=10
    )
    assert short[0].vague is True  # "tiny" is 4 chars < 10


# --------------------------------------------------------------------------- #
# Drift zone 3 — em-dash AND hyphen both accepted
# --------------------------------------------------------------------------- #

_LONG = "a sufficiently long and specific rationale exceeding forty chars"


def test_coverage_override_accepts_em_dash() -> None:
    line = f"coverage-ratchet-acknowledged: scripts/x.py — {_LONG}"
    overrides = parse_overrides(line, COVERAGE_OVERRIDE_RE)
    assert len(overrides) == 1
    assert overrides[0].target == "scripts/x.py"
    assert overrides[0].vague is False


def test_coverage_override_accepts_plain_hyphen() -> None:
    line = f"coverage-ratchet-acknowledged: scripts/x.py - {_LONG}"
    overrides = parse_overrides(line, COVERAGE_OVERRIDE_RE)
    assert len(overrides) == 1
    assert overrides[0].target == "scripts/x.py"
    assert overrides[0].vague is False


def test_mutation_override_accepts_both_separators() -> None:
    em = parse_overrides(f"mutation-ratchet-acknowledged: pkg.mod — {_LONG}", MUTATION_OVERRIDE_RE)
    hy = parse_overrides(f"mutation-ratchet-acknowledged: pkg.mod - {_LONG}", MUTATION_OVERRIDE_RE)
    assert em and hy
    assert em[0].target == "pkg.mod"
    assert hy[0].target == "pkg.mod"


def test_override_short_reason_marked_vague() -> None:
    line = "coverage-ratchet-acknowledged: scripts/x.py — WIP"
    overrides = parse_overrides(line, COVERAGE_OVERRIDE_RE)
    assert overrides[0].vague is True


def test_make_override_re_custom_keyword() -> None:
    rx = make_override_re("custom-ratchet-acknowledged")
    out = parse_overrides(f"custom-ratchet-acknowledged: a/b.py — {_LONG}", rx)
    assert out == [Override(target="a/b.py", reason=_LONG, vague=False)]


def test_override_re_ignores_non_matching_lines() -> None:
    text = "fix: something\nrandom commit body\nno acknowledgement here"
    assert parse_overrides(text, COVERAGE_OVERRIDE_RE) == []


def test_override_re_parses_one_line_in_a_full_commit_message() -> None:
    text = (
        "feat(x): do a thing\n\n"
        "Body explaining the change.\n\n"
        f"coverage-ratchet-acknowledged: scripts/dead.py — {_LONG}\n"
    )
    overrides = parse_overrides(text, COVERAGE_OVERRIDE_RE)
    assert len(overrides) == 1
    assert overrides[0].target == "scripts/dead.py"


# --------------------------------------------------------------------------- #
# Drift zone 2 — ONE suppression grammar (superset; NOSONAR in both sets)
# --------------------------------------------------------------------------- #


def test_nosonar_in_substring_set() -> None:
    assert "# NOSONAR" in SUPPRESSION_PATTERNS
    assert "// NOSONAR" in SUPPRESSION_PATTERNS


def test_superset_substring_markers_present() -> None:
    for marker in ("# pragma: no cover", "# noqa:", "// noqa:", "# type: ignore", "# nosec"):
        assert marker in SUPPRESSION_PATTERNS


def test_contains_suppression_flags_any_marker() -> None:
    assert contains_suppression("x = 1  # NOSONAR — rationale here") is True
    assert contains_suppression("y = requests.get(u)  # noqa: BLE001 — ctx") is True
    assert contains_suppression("z = 1  # pragma: no cover") is True
    assert contains_suppression("plain = 1  # ordinary comment") is False


def test_nosonar_in_bare_pattern_set() -> None:
    # A bare NOSONAR (no rationale) must match a bare-pattern regex.
    assert any(p.search("x = 1  # NOSONAR") for p in BARE_SUPPRESSION_PATTERNS)


def test_bare_suppression_no_rationale_flagged() -> None:
    assert is_bare_suppression("x = 1  # NOSONAR") is True
    assert is_bare_suppression("y = 1  # noqa") is True
    assert is_bare_suppression("y = 1  # noqa: BLE001") is True
    assert is_bare_suppression("z = 1  # pragma: no cover") is True
    assert is_bare_suppression("w = 1  # type: ignore") is True
    assert is_bare_suppression("w = 1  # type: ignore[arg-type]") is True
    assert is_bare_suppression("v = 1  # nosec") is True
    assert is_bare_suppression("v = 1  # nosec B607") is True


def test_suppression_with_rationale_passes() -> None:
    # A same-line rationale after the token means NOT bare → no match.
    assert is_bare_suppression("x = 1  # NOSONAR — internal log path; not user-controlled") is False
    assert is_bare_suppression("y = 1  # noqa: BLE001 — caller pins context") is False
    assert is_bare_suppression("z = 1  # pragma: no cover — defensive import branch") is False
    assert is_bare_suppression("w = 1  # type: ignore[arg-type] — third-party stub gap") is False
    assert is_bare_suppression("v = 1  # nosec B607 — fixed argv, no shell") is False


def test_bare_patterns_are_compiled_regexes() -> None:
    assert all(isinstance(p, re.Pattern) for p in BARE_SUPPRESSION_PATTERNS)
