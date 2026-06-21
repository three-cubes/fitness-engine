"""Tests for the CORE check unused_params_named (v0.6.0)."""

from __future__ import annotations

import ast
from pathlib import Path

from tc_fitness.core_checks.unused_params_named import (
    UnusedParamsNamed,
    build,
    main,
    module_has_unused_param,
)

_UNUSED = """
def handle(event, context):
    return event.id
"""

_NAMED = """
def handle(event, _context):
    return event.id
"""

_ABSTRACT = """
from abc import abstractmethod

class Base:
    @abstractmethod
    def fetch(self, query):
        ...
"""


def _seed(tmp_path: Path, rel: str, body: str) -> Path:
    p = tmp_path / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body, encoding="utf-8")
    return p


def test_detection_core_flags_unused(tmp_path: Path) -> None:
    p = _seed(tmp_path, "u.py", _UNUSED)
    assert module_has_unused_param(p) is True


def test_underscore_named_is_clean(tmp_path: Path) -> None:
    p = _seed(tmp_path, "n.py", _NAMED)
    assert module_has_unused_param(p) is False


def test_abstractmethod_is_exempt(tmp_path: Path) -> None:
    p = _seed(tmp_path, "a.py", _ABSTRACT)
    assert module_has_unused_param(p) is False


def test_self_and_args_kwargs_exempt(tmp_path: Path) -> None:
    body = "def f(self, *args, **kwargs):\n    return 1\n"
    p = _seed(tmp_path, "x.py", body)
    assert module_has_unused_param(p) is False


def test_property_setter_value_exempt(tmp_path: Path) -> None:
    body = "class C:\n    @x.setter\n    def x(self, value):\n        pass\n"
    p = _seed(tmp_path, "s.py", body)
    assert module_has_unused_param(p) is False


def test_rule_from_config_scopes_roots(tmp_path: Path) -> None:
    _seed(tmp_path, "src/u.py", _UNUSED)
    _seed(tmp_path, "vendor/u.py", _UNUSED)
    rule = UnusedParamsNamed.from_config({"roots": ["src"]}, repo_root=tmp_path)
    assert {str(p) for p in rule.collect_violations()} == {"src/u.py"}


def test_run_then_establish_grandfathers(tmp_path: Path) -> None:
    _seed(tmp_path, "src/u.py", _UNUSED)
    rule = build({"roots": ["src"]}, repo_root=tmp_path)
    assert rule.run() == 1
    rule.establish_baseline()
    assert rule.run() == 0


def test_main_establish_baseline_mode(tmp_path: Path) -> None:
    _seed(tmp_path, "u.py", _UNUSED)
    rc = main(["--establish-baseline", "--repo-root", str(tmp_path)])
    assert rc == 0
    assert (tmp_path / ".architecture" / "baseline" / "unused-params-named-files.txt").exists()


def test_no_repo_strings_in_executable_code() -> None:
    import tc_fitness.core_checks.unused_params_named as mod

    text = Path(mod.__file__).read_text(encoding="utf-8")
    tree = ast.parse(text)
    docstring_ids: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Module | ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            doc = ast.get_docstring(node, clean=False)
            if doc is not None and node.body:
                first = node.body[0]
                if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant):
                    docstring_ids.add(id(first.value))
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docstring_ids:
            lowered = node.value.lower()
            for tok in ("kairix", "tc-agent-zone", "agent-zone", "kata"):
                assert tok not in lowered, f"repo identity leaked: {tok}"
