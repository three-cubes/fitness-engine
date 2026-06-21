"""Tests for the ``[tool.tc_fitness]`` gate-config loader.

The config is the repo-owned declaration of the gate. These tests pin:

- resolution order (``.tc-fitness.toml`` wins over ``pyproject.toml``);
- the ``[tool.tc_fitness]`` sub-table extraction from a real pyproject;
- the dedicated-file whole-document shape;
- per-step validation (exactly one of run/shell/catalogue; id required;
  catalogue must be ``module:attr``; dispatch vocabulary; env shape);
- duplicate-id rejection;
- every error is agent-actionable (carries ``fix:`` and ``next:``).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tc_fitness.gate_config import (
    GateConfigError,
    find_config_file,
    load_config,
    parse_config,
)

# --------------------------------------------------------------------------- #
# resolution
# --------------------------------------------------------------------------- #


def test_dedicated_file_wins_over_pyproject(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text("[tool.tc_fitness]\n")
    (tmp_path / ".tc-fitness.toml").write_text("name = 'x'\n")
    assert find_config_file(tmp_path) == tmp_path / ".tc-fitness.toml"


def test_pyproject_used_when_no_dedicated_file(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text("[tool.tc_fitness]\n")
    assert find_config_file(tmp_path) == tmp_path / "pyproject.toml"


def test_no_config_file_returns_none(tmp_path: Path) -> None:
    assert find_config_file(tmp_path) is None


def test_load_missing_config_is_actionable(tmp_path: Path) -> None:
    with pytest.raises(GateConfigError) as exc:
        load_config(tmp_path)
    msg = str(exc.value)
    assert "fix:" in msg and "next:" in msg


# --------------------------------------------------------------------------- #
# pyproject extraction
# --------------------------------------------------------------------------- #


def test_load_from_pyproject_sub_table(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text(
        "[project]\nname = 'demo'\n\n"
        "[tool.tc_fitness]\n"
        'name = "demo gate"\n'
        "fail_fast = true\n\n"
        "[[tool.tc_fitness.steps]]\n"
        'id = "ruff"\n'
        'summary = "ruff lint"\n'
        'run = ["ruff", "check", "."]\n'
    )
    cfg = load_config(tmp_path)
    assert cfg.name == "demo gate"
    assert cfg.fail_fast is True
    assert len(cfg.steps) == 1
    step = cfg.steps[0]
    assert step.id == "ruff"
    assert step.kind == "run"
    assert step.run == ("ruff", "check", ".")


def test_pyproject_without_tc_fitness_block_is_actionable(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text("[project]\nname = 'demo'\n")
    with pytest.raises(GateConfigError) as exc:
        load_config(tmp_path)
    assert "[tool.tc_fitness]" in str(exc.value)
    assert "fix:" in str(exc.value)


def test_dedicated_file_whole_document_is_config(tmp_path: Path) -> None:
    (tmp_path / ".tc-fitness.toml").write_text(
        'name = "dedicated"\n\n[[steps]]\nid = "tests"\nshell = "pytest -q"\n'
    )
    cfg = load_config(tmp_path)
    assert cfg.name == "dedicated"
    assert cfg.steps[0].kind == "shell"
    assert cfg.steps[0].shell == "pytest -q"


# --------------------------------------------------------------------------- #
# step validation
# --------------------------------------------------------------------------- #


def parse_config_table(steps_block: str, tmp_path: Path):
    import tomllib

    src = tmp_path / "pyproject.toml"
    # Qualify a bare `[[steps]]` array to `[[tool.tc_fitness.steps]]` so it nests
    # under the table rather than opening a new top-level array (TOML closes the
    # `[tool.tc_fitness]` header the moment a `[[steps]]` line appears).
    qualified = steps_block.replace("[[steps]]", "[[tool.tc_fitness.steps]]")
    doc = "[tool.tc_fitness]\n" + qualified
    table = tomllib.loads(doc)["tool"]["tc_fitness"]
    return parse_config(table, source=src)


def test_no_steps_is_actionable(tmp_path: Path) -> None:
    with pytest.raises(GateConfigError) as exc:
        parse_config_table("name = 'x'\n", tmp_path)
    assert "steps" in str(exc.value)
    assert "fix:" in str(exc.value)


def test_step_requires_exactly_one_action(tmp_path: Path) -> None:
    with pytest.raises(GateConfigError) as exc:
        parse_config_table("[[steps]]\nid = 'x'\nrun = ['a']\nshell = 'b'\n", tmp_path)
    assert "EXACTLY ONE" in str(exc.value)


def test_step_with_no_action_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(GateConfigError):
        parse_config_table("[[steps]]\nid = 'x'\n", tmp_path)


def test_step_missing_id_is_actionable(tmp_path: Path) -> None:
    with pytest.raises(GateConfigError) as exc:
        parse_config_table("[[steps]]\nrun = ['a']\n", tmp_path)
    assert "id" in str(exc.value)


def test_catalogue_must_be_module_attr(tmp_path: Path) -> None:
    with pytest.raises(GateConfigError) as exc:
        parse_config_table("[[steps]]\nid = 'cat'\ncatalogue = 'not_a_ref'\n", tmp_path)
    assert "module.path:attr" in str(exc.value)


def test_valid_catalogue_step_parses(tmp_path: Path) -> None:
    cfg = parse_config_table(
        "[[steps]]\n"
        "id = 'cat'\n"
        "catalogue = 'scripts.checks._rule_catalogue:ALL_ENTRIES'\n"
        "checks_dir = 'scripts/checks'\n"
        "dispatch = 'subprocess'\n"
        "parallel = true\n",
        tmp_path,
    )
    step = cfg.steps[0]
    assert step.kind == "catalogue"
    assert step.catalogue == "scripts.checks._rule_catalogue:ALL_ENTRIES"
    assert step.checks_dir == "scripts/checks"
    assert step.dispatch == "subprocess"
    assert step.parallel is True


def test_invalid_dispatch_is_actionable(tmp_path: Path) -> None:
    with pytest.raises(GateConfigError) as exc:
        parse_config_table(
            "[[steps]]\nid = 'c'\ncatalogue = 'm:A'\ndispatch = 'threads'\n",
            tmp_path,
        )
    assert "dispatch" in str(exc.value)


def test_run_must_be_list_of_strings(tmp_path: Path) -> None:
    with pytest.raises(GateConfigError) as exc:
        parse_config_table("[[steps]]\nid = 'x'\nrun = 'ruff check'\n", tmp_path)
    assert "list of strings" in str(exc.value)


def test_env_must_be_string_table(tmp_path: Path) -> None:
    with pytest.raises(GateConfigError) as exc:
        parse_config_table("[[steps]]\nid = 'x'\nrun = ['a']\nenv = { K = 1 }\n", tmp_path)
    assert "env" in str(exc.value)


def test_optional_step_fields_default(tmp_path: Path) -> None:
    cfg = parse_config_table("[[steps]]\nid = 'x'\nrun = ['a']\n", tmp_path)
    step = cfg.steps[0]
    assert step.cwd == "."
    assert step.env == {}
    assert step.allow_missing is False
    assert step.continue_on_error is False
    assert step.fix == ""
    assert step.next == ""


def test_step_fix_next_and_flags_parse(tmp_path: Path) -> None:
    cfg = parse_config_table(
        "[[steps]]\n"
        "id = 'x'\n"
        "run = ['a']\n"
        "allow_missing = true\n"
        "continue_on_error = true\n"
        'fix = "do the thing"\n'
        'next = "re-run"\n'
        'env = { K = "v" }\n',
        tmp_path,
    )
    step = cfg.steps[0]
    assert step.allow_missing is True
    assert step.continue_on_error is True
    assert step.fix == "do the thing"
    assert step.next == "re-run"
    assert step.env == {"K": "v"}


def test_duplicate_step_ids_rejected(tmp_path: Path) -> None:
    with pytest.raises(GateConfigError) as exc:
        parse_config_table(
            "[[steps]]\nid = 'x'\nrun = ['a']\n[[steps]]\nid = 'x'\nrun = ['b']\n",
            tmp_path,
        )
    assert "duplicate" in str(exc.value)


def test_malformed_toml_is_actionable(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text("[tool.tc_fitness\n")  # broken
    with pytest.raises(GateConfigError) as exc:
        load_config(tmp_path)
    assert "fix:" in str(exc.value)
