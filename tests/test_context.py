"""Tests for the shared CheckContext (file index + AST parse/walk cache).

The parse-once invariant is the reason the in-process runner is fast: every
file is ``ast.parse``-d at most once per run, no matter how many rules inspect
it. These tests pin that invariant, the source-text cache key (an edited file
gets a fresh tree), the walk cache, and the install/restore boundary.
"""

from __future__ import annotations

import ast
from pathlib import Path

from tc_fitness.context import CheckContext


def test_python_files_indexes_and_skips_pycache(tmp_path: Path) -> None:
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "a.py").write_text("")
    (tmp_path / "pkg" / "__pycache__").mkdir()
    (tmp_path / "pkg" / "__pycache__" / "c.py").write_text("")
    ctx = CheckContext(repo_root=tmp_path)
    found = {p.name for p in ctx.python_files("pkg")}
    assert found == {"a.py"}


def test_python_files_memoised(tmp_path: Path) -> None:
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "a.py").write_text("")
    ctx = CheckContext(repo_root=tmp_path)
    first = ctx.python_files("pkg")
    second = ctx.python_files("pkg")
    assert first is second  # same tuple object returned (memoised)


def test_parse_cache_parses_each_source_once(tmp_path: Path) -> None:
    ctx = CheckContext(repo_root=tmp_path)
    source = "x = 1\n"
    t1 = ctx.parse(source, filename="f.py")
    t2 = ctx.parse(source, filename="f.py")
    assert t1 is t2  # identical tree object
    assert ctx.parse_misses == 1  # parsed once
    assert ctx.parse_hits == 1  # served from cache once


def test_parse_cache_key_includes_source_text(tmp_path: Path) -> None:
    # Same filename, DIFFERENT source ⇒ a fresh parse (no stale tree).
    ctx = CheckContext(repo_root=tmp_path)
    t1 = ctx.parse("x = 1\n", filename="f.py")
    t2 = ctx.parse("x = 2\n", filename="f.py")
    assert t1 is not t2
    assert ctx.parse_misses == 2


def test_walk_cache_shares_node_list_by_tree_identity(tmp_path: Path) -> None:
    ctx = CheckContext(repo_root=tmp_path)
    tree = ctx.parse("def f():\n    return 1\n", filename="f.py")
    w1 = ctx.walk(tree)
    w2 = ctx.walk(tree)
    assert w1 is w2
    assert ctx.walk_misses == 1
    assert ctx.walk_hits == 1
    # Order is the stdlib BFS order.
    assert w1 == list(ast.walk(tree)) or [type(n).__name__ for n in w1] == [
        type(n).__name__ for n in ast.walk(tree)
    ]


def test_install_patches_and_restores_ast_parse(tmp_path: Path) -> None:
    real_parse = ast.parse
    real_walk = ast.walk
    ctx = CheckContext(repo_root=tmp_path)
    with ctx.install():
        # Inside the context, ast.parse routes through the cache.
        ast.parse("y = 1\n")
        ast.parse("y = 1\n")
        assert ctx.parse_hits >= 1
    # Restored on exit.
    assert ast.parse is real_parse
    assert ast.walk is real_walk


def test_install_passes_exotic_parse_through_uncached(tmp_path: Path) -> None:
    ctx = CheckContext(repo_root=tmp_path)
    with ctx.install():
        # An 'eval' mode parse is exotic → bypasses the cache, no miss recorded.
        node = ast.parse("1 + 1", mode="eval")
    assert isinstance(node, ast.Expression)
    assert ctx.parse_misses == 0  # exotic call never touched the cache


def test_tree_for_returns_none_on_syntax_error(tmp_path: Path) -> None:
    bad = tmp_path / "bad.py"
    bad.write_text("def (:\n")  # syntax error
    ctx = CheckContext(repo_root=tmp_path)
    assert ctx.tree_for(bad) is None


def test_source_for_caches_and_tolerates_missing(tmp_path: Path) -> None:
    f = tmp_path / "x.py"
    f.write_text("z = 3\n")
    ctx = CheckContext(repo_root=tmp_path)
    assert ctx.source_for(f) == "z = 3\n"
    assert ctx.source_for(tmp_path / "missing.py") is None


def test_distinct_files_parsed_is_the_miss_count(tmp_path: Path) -> None:
    ctx = CheckContext(repo_root=tmp_path)
    ctx.parse("a = 1\n", filename="a.py")
    ctx.parse("b = 2\n", filename="b.py")
    ctx.parse("a = 1\n", filename="a.py")  # cache hit
    assert ctx.distinct_files_parsed == 2
