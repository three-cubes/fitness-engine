"""CORE check: no-internal-monkeypatch — no patching of own internals in tests.

A test that patches its OWN package's internals (``@patch("myapp.x.y")``,
``monkeypatch.setattr(myapp.x, "y", fake)``, or a direct
``myapp.x.y = fake`` assignment) tests the fake, not the composition. The
canonical seam is constructor injection of a fake at a boundary class. This
rule walks each in-scope test file via the AST and flags six shapes that
substitute an internal implementation:

1. ``@patch("<pkg>.X.Y", ...)`` decorator
2. ``with patch("<pkg>.X.Y", ...):`` context manager
3. ``<pkg>.X.Y = <expr>`` full-path attribute assignment
4. ``<alias>.Y = <expr>`` where ``<alias>`` resolves to an internal module
5. ``monkeypatch.setattr("<pkg>.X.Y", ...)`` string-target form
6. ``monkeypatch.setattr(<internal module ref>, "attr", fake)`` ref-target form

Stdlib and external-SDK roots are exempt -- patching those is fixturing
genuinely external state at the application edge. An assignment inside a
``with pytest.raises(...):`` block is exempt too: that pins that the patch
path is BLOCKED (e.g. a frozen dataclass), the opposite of monkey-patching.

Ported from kairix ``scripts/checks/check_no_internal_patches.py`` (F1) and
re-expressed as a configurable, repo-agnostic rule: the internal package
roots and the exempt (stdlib / SDK) roots arrive from config -- NO repo
package name is baked in. (A rule bound with no internal packages matches
nothing.)
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
        "rewrite the test to construct the unit under test with a fake "
        "injected at a boundary (e.g. SearchPipeline(retriever=FakeRetriever(...))). "
        "If the production class lacks a constructor seam, add one. When "
        "production resolves dependencies via call-time local imports, move "
        "that resolution to construction time via a Deps dataclass with "
        "default_factory and inject the fake there."
    ),
    nxt="re-run this check to confirm it goes green.",
    run="python -m tc_fitness.core_checks.no_internal_monkeypatch",
    passing="pipeline = SearchPipeline(retriever=FakeRetriever(hits=[...]))",
    forbidden='@patch("myapp.core.search.run")  # patches own internals',
)


def _matches_internal(name: str, internal_packages: tuple[str, ...]) -> bool:
    """True iff ``name`` is one of the internal packages or a dotted child."""
    return any(name == pkg or name.startswith(f"{pkg}.") for pkg in internal_packages)


def _resolve_internal_aliases(tree: ast.AST, internal_packages: tuple[str, ...]) -> dict[str, str]:
    """Map local name -> fully-qualified internal path from the file's imports."""
    aliases: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if not _matches_internal(alias.name, internal_packages):
                    continue
                if alias.asname:
                    aliases[alias.asname] = alias.name
                else:
                    root = alias.name.split(".")[0]
                    aliases[root] = root
        elif isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            if not _matches_internal(mod, internal_packages):
                continue
            for alias in node.names:
                local = alias.asname or alias.name
                aliases[local] = f"{mod}.{alias.name}" if mod else alias.name
    return aliases


def _attribute_root_name(node: ast.expr) -> str | None:
    cur = node
    while isinstance(cur, ast.Attribute):
        cur = cur.value
    if isinstance(cur, ast.Name):
        return cur.id
    return None


def _resolves_to_internal(
    expr: ast.expr,
    aliases: dict[str, str],
    internal_packages: tuple[str, ...],
    exempt_roots: frozenset[str],
) -> bool:
    """Does ``expr`` (a Name or Attribute) resolve to an internal module?"""
    package_roots = {pkg.split(".")[0] for pkg in internal_packages}
    if isinstance(expr, ast.Name):
        if expr.id in package_roots:
            return True
        return expr.id in aliases and _matches_internal(aliases[expr.id], internal_packages)
    if isinstance(expr, ast.Attribute):
        root = _attribute_root_name(expr)
        if root is None:
            return False
        if root in package_roots:
            return True
        if root in exempt_roots:
            return False
        return root in aliases and _matches_internal(aliases[root], internal_packages)
    return False


def _is_patch_call(node: ast.expr) -> bool:
    """Call to ``patch`` / ``mock.patch`` etc. (NOT ``patch.dict`` / ``patch.object``)."""
    if not isinstance(node, ast.Call):
        return False
    func = node.func
    if isinstance(func, ast.Name):
        return func.id == "patch"
    if isinstance(func, ast.Attribute):
        return func.attr == "patch"
    return False


def _first_arg_is_internal_string(call: ast.expr, internal_packages: tuple[str, ...]) -> bool:
    """First positional arg is a string literal naming an internal module."""
    if not isinstance(call, ast.Call) or not call.args:
        return False
    first = call.args[0]
    if isinstance(first, ast.Constant) and isinstance(first.value, str):
        return _matches_internal(first.value, internal_packages)
    return False


def _is_monkeypatch_setattr(node: ast.Call) -> bool:
    func = node.func
    return (
        isinstance(func, ast.Attribute)
        and func.attr == "setattr"
        and isinstance(func.value, ast.Name)
        and func.value.id == "monkeypatch"
    )


def _is_inside_pytest_raises(parent_map: dict[ast.AST, ast.AST], node: ast.AST) -> bool:
    """True iff ``node`` is lexically inside a ``with pytest.raises(...):`` block."""
    current: ast.AST | None = node
    while current is not None:
        if isinstance(current, ast.With):
            for item in current.items:
                ctx = item.context_expr
                if isinstance(ctx, ast.Call):
                    func = ctx.func
                    if isinstance(func, ast.Attribute) and func.attr == "raises":
                        return True
                    if isinstance(func, ast.Name) and func.id == "raises":
                        return True
        current = parent_map.get(current)
    return False


def file_has_internal_patch(
    path: Path,
    *,
    internal_packages: tuple[str, ...],
    exempt_roots: frozenset[str],
) -> bool:
    """True iff ``path`` contains any of the six internal-patch shapes.

    Pure helper (the detection core). A syntax / decode error is treated as
    "no violation" (another check owns unparseable files).
    """
    if not internal_packages:
        return False
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (SyntaxError, UnicodeDecodeError, OSError):
        return False

    aliases = _resolve_internal_aliases(tree, internal_packages)
    parent_map: dict[ast.AST, ast.AST] = {}
    for parent in ast.walk(tree):
        for child in ast.iter_child_nodes(parent):
            parent_map[child] = parent

    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            for deco in node.decorator_list:
                if _is_patch_call(deco) and _first_arg_is_internal_string(deco, internal_packages):
                    return True

        if isinstance(node, ast.With):
            for item in node.items:
                ctx = item.context_expr
                if _is_patch_call(ctx) and _first_arg_is_internal_string(ctx, internal_packages):
                    return True

        if isinstance(node, ast.Assign):
            for target in node.targets:
                if (
                    isinstance(target, ast.Attribute)
                    and _resolves_to_internal(target, aliases, internal_packages, exempt_roots)
                    and not _is_inside_pytest_raises(parent_map, node)
                ):
                    return True

        if isinstance(node, ast.Call) and _is_monkeypatch_setattr(node):
            if _first_arg_is_internal_string(node, internal_packages):
                return True
            if node.args and _resolves_to_internal(node.args[0], aliases, internal_packages, exempt_roots):
                return True

    return False


class NoInternalMonkeypatch(FitnessRule):
    """Flags test files that patch their own package's internals."""

    name = "no-internal-monkeypatch"
    remediation = REMEDIATION
    extensions = (".py",)

    #: Internal package roots to protect -- repo-supplied, no default identity.
    internal_packages: tuple[str, ...] = ()
    #: Stdlib / external-SDK roots whose patching is a legitimate boundary fake.
    exempt_roots: frozenset[str] = frozenset()

    @classmethod
    def from_config(
        cls,
        config: Mapping[str, Any],
        *,
        repo_root: Path | None = None,
    ) -> NoInternalMonkeypatch:
        rule = super().from_config(config, repo_root=repo_root)
        assert isinstance(rule, NoInternalMonkeypatch)  # noqa: S101  # narrowing for mypy
        packages = config.get("internal_packages")
        rule.internal_packages = tuple(packages) if packages is not None else ()
        exempt = config.get("exempt_roots")
        rule.exempt_roots = frozenset(exempt) if exempt is not None else frozenset()
        return rule

    def file_has_violation(self, path: Path) -> bool:
        return file_has_internal_patch(
            path,
            internal_packages=self.internal_packages,
            exempt_roots=self.exempt_roots,
        )


def build(config: Mapping[str, Any], *, repo_root: Path | None = None) -> NoInternalMonkeypatch:
    """Factory the engine calls to bind this CORE check to a consumer's config."""
    return NoInternalMonkeypatch.from_config(config, repo_root=repo_root)


def main(argv: list[str] | None = None) -> int:
    """CLI entry — supports ``--establish-baseline`` and ``--repo-root``."""
    return run_core_check(NoInternalMonkeypatch, argv)


if __name__ == "__main__":
    import sys

    sys.exit(main())
