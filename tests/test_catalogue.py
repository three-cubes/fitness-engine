"""Tests for the repo-agnostic catalogue schema (RuleEntry)."""

from __future__ import annotations

from tc_fitness.catalogue import RuleEntry, is_dispatchable


def test_rule_entry_accepts_fnumber_id() -> None:
    e = RuleEntry(id="F26", gate="f26", check="provider_layer_imports")
    assert e.id == "F26"


def test_rule_entry_accepts_descriptive_id() -> None:
    # The schema is id-agnostic — kairix's "F26" and taz's descriptive style
    # are equally valid.
    e = RuleEntry(id="no-duplicate-string", gate="no-duplicate-string", check="no_duplicate_string")
    assert e.id == "no-duplicate-string"


def test_defaults_are_conservative() -> None:
    e = RuleEntry(id="X", gate="x", check="x")
    assert e.status == "shipped"
    assert e.run_all is True
    assert e.staged_class == "file-local"
    assert e.staged_scope is None
    assert e.script is None
    assert e.subprocess_arg_env is None
    # v0.4.0 additive argv-exception fields default-safe.
    assert e.script_path_override is None
    assert e.static_extra_args == ()
    assert e.env_gated_extra_args == ()


def test_is_dispatchable_true_for_shipped() -> None:
    assert is_dispatchable(RuleEntry(id="X", gate="x", check="x")) is True


def test_is_dispatchable_false_for_proposed_status() -> None:
    assert is_dispatchable(RuleEntry(id="X", gate="x", check="x", status="proposed")) is False


def test_is_dispatchable_false_for_proposed_check_placeholder() -> None:
    assert is_dispatchable(RuleEntry(id="X", gate="x", check="(proposed)")) is False


def test_rule_entry_is_frozen() -> None:
    e = RuleEntry(id="X", gate="x", check="x")
    try:
        e.id = "Y"  # type: ignore[misc]
    except Exception as exc:
        assert "cannot assign" in str(exc) or "frozen" in str(exc).lower()
    else:  # pragma: no cover - frozen dataclass must raise
        raise AssertionError("RuleEntry should be frozen")
