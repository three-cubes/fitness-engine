"""Tests for the CORE check empty_body_intent (v0.6.0)."""

from __future__ import annotations

import ast
from pathlib import Path

from tc_fitness.core_checks.empty_body_intent import (
    EmptyBodyIntent,
    build,
    main,
    module_has_undocumented_empty_body,
)

_BARE = """
def on_event(self, event):
    pass
"""

_DOCSTRING = '''
def on_event(self, event):
    """No-op default; concrete strategies override this."""
'''

_INTENT_COMMENT = """
def shutdown(self):
    # Intentionally empty - graceful shutdown is a Protocol-required method.
    pass
"""

_ABSTRACT = """
from abc import abstractmethod

class Base:
    @abstractmethod
    def fetch(self):
        ...
"""


def _seed(tmp_path: Path, rel: str, body: str) -> Path:
    p = tmp_path / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body, encoding="utf-8")
    return p


def test_detection_core_flags_bare_pass(tmp_path: Path) -> None:
    p = _seed(tmp_path, "b.py", _BARE)
    assert module_has_undocumented_empty_body(p, marker="Intentionally empty") is True


def test_docstring_satisfies(tmp_path: Path) -> None:
    p = _seed(tmp_path, "d.py", _DOCSTRING)
    assert module_has_undocumented_empty_body(p, marker="Intentionally empty") is False


def test_intent_comment_satisfies(tmp_path: Path) -> None:
    p = _seed(tmp_path, "i.py", _INTENT_COMMENT)
    assert module_has_undocumented_empty_body(p, marker="Intentionally empty") is False


def test_abstractmethod_is_exempt(tmp_path: Path) -> None:
    p = _seed(tmp_path, "a.py", _ABSTRACT)
    assert module_has_undocumented_empty_body(p, marker="Intentionally empty") is False


def test_marker_is_config_driven(tmp_path: Path) -> None:
    body = "def f(self):\n    # DELIBERATE NO-OP for the adapter contract.\n    pass\n"
    p = _seed(tmp_path, "m.py", body)
    # Default marker doesn't match → violation.
    assert module_has_undocumented_empty_body(p, marker="Intentionally empty") is True
    # Configure the consumer's own marker → satisfied.
    rule = build({"roots": ["."], "marker": "DELIBERATE NO-OP"}, repo_root=tmp_path)
    assert rule.file_has_violation(p) is False


def test_rule_from_config_scopes_roots(tmp_path: Path) -> None:
    _seed(tmp_path, "src/b.py", _BARE)
    _seed(tmp_path, "vendor/b.py", _BARE)
    rule = EmptyBodyIntent.from_config({"roots": ["src"]}, repo_root=tmp_path)
    assert {str(p) for p in rule.collect_violations()} == {"src/b.py"}


def test_run_then_establish_grandfathers(tmp_path: Path) -> None:
    _seed(tmp_path, "src/b.py", _BARE)
    rule = EmptyBodyIntent.from_config({"roots": ["src"]}, repo_root=tmp_path)
    assert rule.run() == 1
    rule.establish_baseline()
    assert rule.run() == 0


def test_main_establish_baseline_mode(tmp_path: Path) -> None:
    _seed(tmp_path, "b.py", _BARE)
    rc = main(["--establish-baseline", "--repo-root", str(tmp_path)])
    assert rc == 0
    assert (tmp_path / ".architecture" / "baseline" / "empty-body-intent-files.txt").exists()


def test_no_repo_strings_in_executable_code() -> None:
    import tc_fitness.core_checks.empty_body_intent as mod

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
