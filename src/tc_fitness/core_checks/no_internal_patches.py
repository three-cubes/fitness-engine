"""CORE check: no_internal_patches — tests must not substitute internal modules.

A test that ``monkeypatch.setattr`` / ``unittest.mock.patch`` / assigns onto an
INTERNAL module is inappropriate intimacy: it simulates composition instead of
exercising it, so the suite stays green while production wiring rots. (The
canonical example: ~1583 monkeypatched tests inflating coverage 47%->82%
without adding defect-catching power.) The right unit of work is to construct
the unit under test with explicit fakes passed through its constructor / call
signature — adding a DI seam to production code when one is missing.

Ported from tc-agent-zone ``scripts/checks/no_internal_patches.py`` (itself
kairix F1) and re-expressed as a configurable, repo-agnostic rule. The AST
anti-pattern detection is domain-intrinsic; the two sets that decide what
"internal" means (``internal_roots`` — patching these is the smell) and what is
a legitimate boundary fake (``exempt_roots`` — stdlib + SDK module roots) are
consumer config. The engine ships NO repo package names.
"""

from __future__ import annotations

import ast
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from tc_fitness.core_checks import run_core_check
from tc_fitness.fitness_rule import FitnessRule
from tc_fitness.lib import remediation as _remediation

REMEDIATION = _remediation(
    fix=(
        "rewrite the test to inject a fake (subprocess runner, filesystem "
        "adapter, etc.) through the function/class signature; if the production "
        "code lacks a DI seam, add one. Patching internal modules hides "
        "composition failures."
    ),
    nxt="re-run this check to confirm the file falls off the violator list.",
    run="python -m tc_fitness.core_checks.no_internal_patches",
    passing="runner = ScorecardRunner(subprocess_run=fake_run, fs=FakeFs(...))",
    forbidden="monkeypatch.setattr('scripts.checks.x.PATH', tmp_path)",
)


def _is_internal(dotted: str, internal_roots: frozenset[str]) -> bool:
    return dotted.split(".", 1)[0] in internal_roots


def _record_import_aliases(node: ast.Import, aliases: dict[str, str], internal_roots: frozenset[str]) -> None:
    for alias in node.names:
        if not _is_internal(alias.name, internal_roots):
            continue
        if alias.asname:
            aliases[alias.asname] = alias.name
        else:
            root = alias.name.split(".", 1)[0]
            aliases[root] = root


def _record_import_from_aliases(
    node: ast.ImportFrom, aliases: dict[str, str], internal_roots: frozenset[str]
) -> None:
    mod = node.module or ""
    if not _is_internal(mod, internal_roots):
        return
    for alias in node.names:
        local = alias.asname or alias.name
        aliases[local] = f"{mod}.{alias.name}" if mod else alias.name


def _resolve_internal_aliases(tree: ast.AST, internal_roots: frozenset[str]) -> dict[str, str]:
    aliases: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            _record_import_aliases(node, aliases, internal_roots)
        elif isinstance(node, ast.ImportFrom):
            _record_import_from_aliases(node, aliases, internal_roots)
    return aliases


def _is_dynamic_module_load(call: ast.Call) -> bool:
    """True iff ``call`` returns a dynamically-loaded module reference.

    ``importlib.util.module_from_spec(spec)`` (canonical) or a ``_load()``
    helper (a common test-file convention). Tests that load a private module
    this way and then patch attributes on the result are the dominant theatre
    shape.
    """
    func = call.func
    if isinstance(func, ast.Attribute) and func.attr == "module_from_spec":
        return True
    return isinstance(func, ast.Name) and func.id == "_load"


def _resolve_dynamic_module_vars(tree: ast.AST) -> set[str]:
    dyn: set[str] = set()
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Assign) and isinstance(node.value, ast.Call)):
            continue
        if not _is_dynamic_module_load(node.value):
            continue
        for target in node.targets:
            if isinstance(target, ast.Name):
                dyn.add(target.id)
    return dyn


def _attribute_root_name(node: ast.expr) -> str | None:
    cur: ast.expr = node
    while isinstance(cur, ast.Attribute):
        cur = cur.value
    if isinstance(cur, ast.Name):
        return cur.id
    return None


def _name_resolves_to_internal(
    name: str, aliases: dict[str, str], dyn_vars: set[str], internal_roots: frozenset[str]
) -> bool:
    if name in internal_roots or name in dyn_vars:
        return True
    return name in aliases and _is_internal(aliases[name], internal_roots)


def _root_resolves_to_internal(
    root: str,
    aliases: dict[str, str],
    dyn_vars: set[str],
    internal_roots: frozenset[str],
    exempt_roots: frozenset[str],
) -> bool:
    if root in internal_roots or root in dyn_vars:
        return True
    if root in exempt_roots:
        return False
    return root in aliases and _is_internal(aliases[root], internal_roots)


def _resolves_to_internal(
    expr: ast.expr,
    aliases: dict[str, str],
    dyn_vars: set[str],
    internal_roots: frozenset[str],
    exempt_roots: frozenset[str],
) -> bool:
    if isinstance(expr, ast.Name):
        return _name_resolves_to_internal(expr.id, aliases, dyn_vars, internal_roots)
    if isinstance(expr, ast.Attribute):
        root = _attribute_root_name(expr)
        return root is not None and _root_resolves_to_internal(
            root, aliases, dyn_vars, internal_roots, exempt_roots
        )
    return False


def _is_patch_call(node: ast.expr) -> bool:
    if not isinstance(node, ast.Call):
        return False
    func = node.func
    if isinstance(func, ast.Name):
        return func.id == "patch"
    if isinstance(func, ast.Attribute):
        return func.attr == "patch"
    return False


def _first_arg_is_internal_string(call: ast.expr, internal_roots: frozenset[str]) -> bool:
    if not isinstance(call, ast.Call) or not call.args:
        return False
    first = call.args[0]
    if isinstance(first, ast.Constant) and isinstance(first.value, str):
        return first.value.split(".", 1)[0] in internal_roots
    return False


def _is_monkeypatch_setattr(node: ast.Call) -> bool:
    func = node.func
    return (
        isinstance(func, ast.Attribute)
        and func.attr == "setattr"
        and isinstance(func.value, ast.Name)
        and func.value.id == "monkeypatch"
    )


def _is_pytest_raises_call(ctx: ast.expr) -> bool:
    if not isinstance(ctx, ast.Call):
        return False
    func = ctx.func
    if isinstance(func, ast.Attribute) and func.attr == "raises":
        return True
    return isinstance(func, ast.Name) and func.id == "raises"


def _with_block_is_pytest_raises(with_node: ast.With) -> bool:
    return any(_is_pytest_raises_call(item.context_expr) for item in with_node.items)


def _is_inside_pytest_raises(parent_map: dict[ast.AST, ast.AST], node: ast.AST) -> bool:
    current: ast.AST | None = node
    while current is not None:
        if isinstance(current, ast.With) and _with_block_is_pytest_raises(current):
            return True
        current = parent_map.get(current)
    return False


def _node_violates(
    node: ast.AST,
    aliases: dict[str, str],
    dyn_vars: set[str],
    parent_map: dict[ast.AST, ast.AST],
    internal_roots: frozenset[str],
    exempt_roots: frozenset[str],
) -> bool:
    if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
        if any(
            _is_patch_call(d) and _first_arg_is_internal_string(d, internal_roots)
            for d in node.decorator_list
        ):
            return True
    if isinstance(node, ast.With):
        if any(
            _is_patch_call(item.context_expr)
            and _first_arg_is_internal_string(item.context_expr, internal_roots)
            for item in node.items
        ):
            return True
    if isinstance(node, ast.Assign) and not _is_inside_pytest_raises(parent_map, node):
        if any(
            isinstance(t, ast.Attribute)
            and _resolves_to_internal(t, aliases, dyn_vars, internal_roots, exempt_roots)
            for t in node.targets
        ):
            return True
    if isinstance(node, ast.Call) and _is_monkeypatch_setattr(node):
        if _first_arg_is_internal_string(node, internal_roots):
            return True
        if node.args and _resolves_to_internal(node.args[0], aliases, dyn_vars, internal_roots, exempt_roots):
            return True
    return False


def file_patches_internal(
    path: Path,
    *,
    internal_roots: frozenset[str],
    exempt_roots: frozenset[str],
) -> bool:
    """Pure detection helper: True iff ``path`` exhibits any internal-patch shape.

    A syntax / read error is treated as "no violation" (another check owns
    unparseable files).
    """
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (SyntaxError, OSError):
        return False
    aliases = _resolve_internal_aliases(tree, internal_roots)
    dyn_vars = _resolve_dynamic_module_vars(tree)
    parent_map: dict[ast.AST, ast.AST] = {}
    for parent in ast.walk(tree):
        for child in ast.iter_child_nodes(parent):
            parent_map[child] = parent
    return any(
        _node_violates(node, aliases, dyn_vars, parent_map, internal_roots, exempt_roots)
        for node in ast.walk(tree)
    )


class NoInternalPatches(FitnessRule):
    """Flags Python test files that substitute INTERNAL modules (F1)."""

    name = "no-internal-patches"
    remediation = REMEDIATION
    extensions = (".py",)

    #: Rule-specific config (instance attrs; from_config overrides per consumer).
    #: ``internal_roots`` — package roots whose patching is the smell;
    #: ``exempt_roots`` — stdlib + SDK roots that are legitimate boundary fakes.
    internal_roots: frozenset[str] = frozenset()
    exempt_roots: frozenset[str] = frozenset()

    @classmethod
    def from_config(
        cls,
        config: Mapping[str, Any],
        *,
        repo_root: Path | None = None,
    ) -> NoInternalPatches:
        rule = super().from_config(config, repo_root=repo_root)
        assert isinstance(rule, NoInternalPatches)  # noqa: S101  # narrowing for mypy
        rule.internal_roots = frozenset(config.get("internal_roots", ()))
        rule.exempt_roots = frozenset(config.get("exempt_roots", ()))
        return rule

    def file_has_violation(self, path: Path) -> bool:
        if not self.internal_roots:
            return False
        return file_patches_internal(
            path,
            internal_roots=self.internal_roots,
            exempt_roots=self.exempt_roots,
        )


def build(
    config: Mapping[str, Any],
    *,
    repo_root: Path | None = None,
) -> NoInternalPatches:
    """Factory the engine calls to bind this CORE check to a consumer's config."""
    return NoInternalPatches.from_config(config, repo_root=repo_root)


def main(argv: list[str] | None = None) -> int:
    """CLI entry — supports ``--establish-baseline`` and ``--repo-root``."""
    return run_core_check(NoInternalPatches, argv)


if __name__ == "__main__":
    import sys

    sys.exit(main())
