"""Unified ratchet primitives — the three reconciled drift zones.

Two repos grew parallel ratchet gates (coverage, mutation-survival, sonar-quality)
that drifted apart on three details. This module is the single source for all
three, so a consumer's coverage ratchet and mutation ratchet agree by
construction.

Drift zone 1 — override min-length
----------------------------------
tc-agent-zone's coverage ratchet treated a rationale "vague" below **20** chars
(``<=``); its mutation ratchet used **40** chars (``<``). The remediation text in
*both* gates already advertised "≥40 chars" to the operator — so the coverage
gate's 20 was a latent bug (code disagreed with the message it printed).

Decision: **40 chars, strictly-less-than** (``len(reason) < 40`` is vague).
40 is the stricter superset and matches the contract both repos already
documented to operators. Exposed as :data:`OVERRIDE_MIN_REASON_LEN`.

Drift zone 2 — suppression-pattern list
---------------------------------------
tc-agent-zone added ``NOSONAR`` (and the ``//`` C-style variants) to the
suppression set kairix originally tracked. There were also possessive-quantifier
variations across the regex copies.

Decision: **the superset** of every marker any repo tracked, as one grammar.
Exposed as :data:`SUPPRESSION_PATTERNS` (substring markers, e.g. the
no-production-suppressions sweep) and :data:`BARE_SUPPRESSION_PATTERNS` (compiled
end-of-line regexes, e.g. the rationale gate). ``NOSONAR`` is in both.

Drift zone 3 — override-marker syntax
-------------------------------------
tc-agent-zone's override line regex accepted an em-dash OR a hyphen as the
separator (``[—-]++``); some kairix copies were stricter (em-dash only).

Decision: **accept both** em-dash and hyphen (the superset). A consumer that
wrote ``coverage-ratchet-acknowledged: path - reason`` with a plain hyphen must
keep passing, and so must the em-dash form. Use :func:`make_override_re` to build
a marker parser for any acknowledgement keyword.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# ---------------------------------------------------------------------------
# Drift zone 1 — ONE override-rationale min-length
# ---------------------------------------------------------------------------

#: A ratchet-override rationale shorter than this many characters is "vague".
#: Reconciled to 40 (the stricter of {20, 40}); matches the "≥40 chars" text
#: both repos' remediation messages already printed.
OVERRIDE_MIN_REASON_LEN = 40

#: Lead-in tokens that mark a rationale as vague regardless of length.
#: Union of every variant either repo's VAGUE_OVERRIDE_RE matched.
VAGUE_OVERRIDE_RE = re.compile(
    r"^(wip|minor|todo|skip|later|n/a|out of scope|will[- ]?fix[- ]?later)\b",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Drift zone 3 — ONE override-marker parser (em-dash OR hyphen)
# ---------------------------------------------------------------------------

# Separator class: em-dash (—) OR ASCII hyphen (-), one-or-more, possessive so
# there's no catastrophic backtracking against the trailing reason. This is the
# superset that accepts both repos' historical forms.
_SEP = r"[—-]++"


def make_override_re(keyword: str) -> re.Pattern[str]:
    r"""Build the regex that parses an override acknowledgement line.

    ``keyword`` is the acknowledgement token, e.g. ``"coverage-ratchet-acknowledged"``
    or ``"mutation-ratchet-acknowledged"``. The line shape is::

        <keyword>: <target> <sep> <reason>

    where ``<sep>`` is an em-dash or a hyphen (one or more), matching both repos.
    Two named groups are returned: ``target`` (the path/package) and ``reason``.
    """
    return re.compile(
        rf"^\s*+{re.escape(keyword)}:\s*+(?P<target>\S++)\s*+{_SEP}\s*+(?P<reason>.*)$",
    )


#: Pre-built parsers for the two canonical ratchet keywords.
COVERAGE_OVERRIDE_RE = make_override_re("coverage-ratchet-acknowledged")
MUTATION_OVERRIDE_RE = make_override_re("mutation-ratchet-acknowledged")


@dataclass(frozen=True)
class Override:
    """One parsed acknowledgement line.

    ``target`` is the path (coverage) or package (mutation). ``vague`` is True
    when the reason is too short or matches :data:`VAGUE_OVERRIDE_RE`; vague
    overrides do NOT clear a ratchet failure.
    """

    target: str
    reason: str
    vague: bool


def is_vague_reason(reason: str) -> bool:
    """Return True when ``reason`` is too short or matches the vague lead-in set.

    Trailing dots and surrounding whitespace are stripped before measuring, so
    ``"WIP."`` and ``"   short  "`` are judged on their substance. Uses the
    reconciled ``< OVERRIDE_MIN_REASON_LEN`` threshold (strictly-less-than).
    """
    compact = reason.strip().rstrip(".").strip()
    return len(compact) < OVERRIDE_MIN_REASON_LEN or bool(VAGUE_OVERRIDE_RE.match(compact))


def parse_overrides(text: str, override_re: re.Pattern[str]) -> list[Override]:
    """Parse every acknowledgement line in ``text`` using ``override_re``.

    ``text`` is typically a commit message or PR body. Lines that don't match
    are ignored. Each match becomes an :class:`Override` with ``vague`` computed
    via :func:`is_vague_reason`.
    """
    out: list[Override] = []
    for line in (text or "").splitlines():
        match = override_re.match(line)
        if not match:
            continue
        reason = match.group("reason").strip()
        out.append(Override(target=match.group("target"), reason=reason, vague=is_vague_reason(reason)))
    return out


# ---------------------------------------------------------------------------
# Drift zone 2 — ONE suppression grammar (superset)
# ---------------------------------------------------------------------------

#: Substring suppression markers — the superset of every marker either repo
#: tracked. Used by no-production-suppressions style sweeps that flag any line
#: CONTAINING one of these (rationale or not).
SUPPRESSION_PATTERNS: tuple[str, ...] = (
    "# pragma: no cover",
    "# NOSONAR",
    "// NOSONAR",
    "# noqa:",
    "// noqa:",
    "# type: ignore",
    "# nosec",
)

#: End-of-line "bare suppression" regexes — a suppression token followed only by
#: optional whitespace, i.e. NO trailing rationale. Used by the
#: suppressions-have-rationale gate: a match is a BARE (failing) suppression; a
#: line carrying a same-line rationale after the token does NOT match. Possessive
#: quantifiers keep the code/space classes from backtracking against trailing
#: whitespace. ``NOSONAR`` is included (the tc-agent-zone addition).
BARE_SUPPRESSION_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"#\s*+NOSONAR\s*+$"),
    re.compile(r"#\s*+noqa(?::\s*+[A-Z0-9, ]++)?\s*+$"),
    re.compile(r"#\s*+pragma:\s*+no cover\s*+$"),
    re.compile(r"#\s*+type:\s*+ignore(\[[A-Za-z0-9,_-]+\])?\s*+$"),
    re.compile(r"#\s*+nosec(\s++B\d++|:\s*+B?\d++)?\s*+$"),
)


def contains_suppression(line: str) -> bool:
    """Return True when ``line`` contains any suppression marker (rationale or not)."""
    return any(pattern in line for pattern in SUPPRESSION_PATTERNS)


def is_bare_suppression(line: str) -> bool:
    """Return True when ``line`` ends in a suppression token with NO rationale after it."""
    return any(pattern.search(line) for pattern in BARE_SUPPRESSION_PATTERNS)


__all__ = [
    "OVERRIDE_MIN_REASON_LEN",
    "VAGUE_OVERRIDE_RE",
    "Override",
    "make_override_re",
    "COVERAGE_OVERRIDE_RE",
    "MUTATION_OVERRIDE_RE",
    "is_vague_reason",
    "parse_overrides",
    "SUPPRESSION_PATTERNS",
    "BARE_SUPPRESSION_PATTERNS",
    "contains_suppression",
    "is_bare_suppression",
]
