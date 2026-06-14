# three-cubes-fitness

Shared architecture-fitness primitives for Three Cubes repositories
(`kairix`, `tc-agent-zone`). This package is the **single source** for the
helper code those repos' fitness-function checks previously maintained as two
parallel, slowly-drifting copies. Consuming it means a fix or a behaviour change
lands once, not twice.

It ships two modules:

- **`tc_fitness.lib`** — the merged check helpers:
  - **baseline gating** (from kairix `scripts/checks/_arch_lib.py`):
    `gate()`, `python_files()`, `main_entry()`, `repo_relative()`, `REPO_ROOT`.
  - **agent-actionable emit / YAML** (from tc-agent-zone `scripts/checks/_lib/`):
    `actionable()`, `emit_failures()`, `emit_pass()`, `load_yaml()`, `missing_keys()`.
- **`tc_fitness.ratchet`** — the unified ratchet grammar: one override
  min-length, one marker parser, one suppression grammar (see
  *Drift reconciliation* below).

## What's in the box

```python
from tc_fitness import (
    # baseline gating (kairix surface)
    gate, gate_keys, python_files, main_entry, repo_relative, REPO_ROOT,
    # agent-actionable emit / YAML (tc-agent-zone surface)
    actionable, remediation, emit_failures, emit_pass, load_yaml, missing_keys,
    # unified ratchet primitives
    OVERRIDE_MIN_REASON_LEN, make_override_re, parse_overrides, Override,
    COVERAGE_OVERRIDE_RE, MUTATION_OVERRIDE_RE,
    is_vague_reason, VAGUE_OVERRIDE_RE,
    SUPPRESSION_PATTERNS, BARE_SUPPRESSION_PATTERNS,
    contains_suppression, is_bare_suppression,
)
```

> **v0.2.0 is an additive, backward-compatible superset of v0.1.0.** Every
> v0.1.0 signature and behaviour is unchanged when the new optional parameters
> are left at their defaults. A consumer pinned to `@v0.1.0` keeps working
> unmodified; the additions (`gate_keys`, `remediation`, `actionable(..., run=)`,
> `is_vague_reason(..., min_len=)`, `parse_overrides(..., min_len=)`) exist to
> cover tc-agent-zone's check surface. See *What v0.2.0 adds* below.

### Baseline gating

```python
from pathlib import Path
from tc_fitness import gate, main_entry

# Low-level: gate a pre-computed violation set against
# .architecture/baseline/<name>-files.txt
exit_code = gate("f26-core-no-provider-imports", violations, REMEDIATION)

# Convenience: scan roots, call a per-file predicate, gate the union.
def file_has_violation(path: Path) -> bool: ...
exit_code = main_entry(file_has_violation, "f26", REMEDIATION, "kairix")
```

`REPO_ROOT` defaults to the current working directory (the repo root when checks
run from `safe-commit.sh` / pre-commit / CI). Every gating helper also accepts an
explicit `repo_root=` keyword for test isolation or monorepo sub-trees.

### Agent-actionable output

```python
from tc_fitness import actionable, emit_failures, emit_pass

fails = [actionable("kairix/x.py:12 leaks a secret", "redact it", "re-run check_f15.py")]
if fails:
    emit_failures("f15-no-secret-logging", fails)  # → stderr
else:
    emit_pass("PASS f15-no-secret-logging")        # → stdout
```

### YAML loading

```python
from tc_fitness import load_yaml, missing_keys

data, err = load_yaml(Path("manifest.yaml"))   # (data, None) | (None, "error")
if err is None:
    absent = missing_keys(data, ("name", "version"))
```

`load_yaml` imports PyYAML lazily and returns `(None, "PyYAML missing")` when it
isn't installed, so the dependency is optional — install the `yaml` extra only if
you call it.

## What v0.2.0 adds

v0.2.0 extends the surface to cover tc-agent-zone's 116-check fleet — additively,
so kairix's `@v0.1.0` pin needs no change. Four additions:

### `actionable(what, fix, nxt, run=None)` — optional 3-marker form

59 tc-agent-zone checks emit a `fix:/next:/run:` triple. `actionable` now takes an
optional fourth `run` argument; supplying it appends `; run: <run>`. With `run`
omitted (the default), the output is **byte-identical** to v0.1.0's 2-marker
`<what>; fix: <fix>; next: <nxt>`.

```python
actionable("X broke", "do Y", "rerun Z")                  # X broke; fix: do Y; next: rerun Z
actionable("X broke", "do Y", "rerun Z", "python check.py")  # ...; next: rerun Z; run: python check.py
```

### `remediation(fix, nxt, run, *, passing=None, forbidden=None)` — multiline block

30 tc-agent-zone checks emit a multiline F21-shape remediation block: the three
action markers on their own lines, optionally followed by a `Pass` and a
`Forbidden` example. `remediation` formats that block (no trailing newline),
ready to `print()`.

```python
print(remediation(
    "redact the secret", "re-run the check", "python scripts/checks/check_f15.py",
    passing='logger.info("token redacted")',
    forbidden='logger.info(f"token={token}")',
))
# fix: redact the secret
# next: re-run the check
# run: python scripts/checks/check_f15.py
# Pass: logger.info("token redacted")
# Forbidden: logger.info(f"token={token}")
```

### `gate_keys(name, current, remediation, *, baseline_suffix="-ids.txt")` — string-keyed ratchet

13 tc-agent-zone checks ratchet a baseline whose KEY is a logical id (`-ids.txt`,
e.g. `F30:my_tool`) or a path-glob (`-paths.txt`, e.g. `kairix/**/web/static/*`),
NOT a working-tree file path. `gate()` keys on `Path` objects and *relativises
absolute paths* under `repo_root` — wrong for opaque string keys. `gate_keys` is
its string-keyed sibling: same net-new-fails / shrinks-only / grandfather
semantics and the same exit-code contract, but keys are treated as opaque
strings (no `Path` coercion). `baseline_suffix` selects `-ids.txt` (default) or
`-paths.txt`.

```python
exit_code = gate_keys("f30", {"F30:my_new_tool"}, REMEDIATION)                     # → f30-ids.txt
exit_code = gate_keys("f89", static_globs, REMEDIATION, baseline_suffix="-paths.txt")  # → f89-paths.txt
```

### `min_len` floor override on the ratchet vagueness check

`is_vague_reason` and `parse_overrides` now take an optional keyword-only
`min_len`, defaulting to `OVERRIDE_MIN_REASON_LEN` (=40). tc-agent-zone's shell
directives use a 10-char floor, so its checks call `min_len=10`. The constant is
unchanged and the default-arg behaviour is byte-identical to v0.1.0 — the lower
floor is a per-call choice, never a mutation of the shared default kairix depends
on.

```python
is_vague_reason("x" * 10)               # True  — vague at the default 40-floor
is_vague_reason("x" * 10, min_len=10)   # False — clears taz's 10-floor
```

### Discovery helpers (`REPO_ROOT` / `python_files` / `repo_relative`) cover taz unchanged

tc-agent-zone reimplements `REPO_ROOT = Path(__file__).resolve().parents[2]` inline
in each check. The package's CWD-anchored `REPO_ROOT = Path.cwd()` is the correct
shared replacement: it resolves to the consumer repo root in the `safe-commit.sh`
/ pre-commit / CI invocation paths (where checks run *from* the repo root), and
every gating helper accepts an explicit `repo_root=` for the rare case that
assumption doesn't hold. No additive gap was found here — `python_files`,
`repo_relative`, and `main_entry` already cover taz's `.py` discovery.

## How repositories consume it

Pin to a tag in your `pyproject.toml` (git install — no PyPI publish):

```toml
[project.optional-dependencies]
dev = [
  # kairix stays on v0.1.0 (the additions are a no-op for it); tc-agent-zone
  # pins v0.2.0 for the gate_keys / remediation / run-marker / min_len surface.
  "three-cubes-fitness @ git+https://github.com/three-cubes/fitness-engine.git@v0.2.0",
]
```

or, equivalently, on the command line:

```bash
pip install "three-cubes-fitness @ git+https://github.com/three-cubes/fitness-engine.git@v0.2.0"
```

Always pin a tag, never `@main` — the version is the contract the gates depend on.
Because v0.2.0 is an additive superset, a consumer already pinned to `@v0.1.0`
keeps working unchanged; bump to `@v0.2.0` only when you need the new surface.

## Drift reconciliation

Both repos independently grew the same ratchet gates (coverage, mutation-survival,
sonar-quality) and drifted on three details. This package resolves each to one
behaviour. The merged version is the **superset-correct** choice — it satisfies
every call pattern either repo relied on.

### 1. Override-rationale minimum length → **40 chars, strictly-less-than**

tc-agent-zone's coverage ratchet treated a rationale as "vague" below **20**
chars; its mutation ratchet used **40**. The *remediation text both gates printed
to operators already said "≥40 chars"* — so the 20-char path was a latent bug
(code disagreed with its own message). Reconciled to `OVERRIDE_MIN_REASON_LEN =
40`, and `len(reason) < 40` is vague. Stricter of the two, and matches the
documented contract. **Mutation's behaviour won.**

### 2. Suppression-pattern list → **the superset, one grammar**

tc-agent-zone added `NOSONAR` (and the `//` C-style variants) to the marker set
kairix originally tracked, and the regex copies had possessive-quantifier
variations. Reconciled to the **union** of every marker any repo tracked:
`SUPPRESSION_PATTERNS` (substring markers for "flag any line containing one") and
`BARE_SUPPRESSION_PATTERNS` (end-of-line regexes for "bare suppression, no
rationale"). `NOSONAR` is in both. **The superset won** — dropping any marker
would silently un-gate a suppression one repo was catching.

### 3. Override-marker separator → **em-dash *and* hyphen both accepted**

tc-agent-zone's override-line regex accepted an em-dash **or** an ASCII hyphen as
the path↔reason separator (`[—-]++`); some kairix copies were em-dash-only.
Reconciled to **accept both** (`make_override_re` builds the parser; the
separator class is `[—-]++`, possessive to avoid backtracking). A commit that
wrote `coverage-ratchet-acknowledged: path - reason` with a plain hyphen must keep
clearing the ratchet, and so must the em-dash form. **The superset (tc-agent-zone's
looser parse) won.**

## Development

```bash
pip install -e ".[dev]"
python -m pytest tests/ -q
```

The test suite is the proof the merge is behaviour-preserving: `tests/test_lib.py`
pins the call patterns each repo's checks depend on, and `tests/test_ratchet.py`
pins the three reconciled drift decisions (40-char threshold; em-dash AND hyphen;
`NOSONAR` in the suppression set).

The package is self-contained: pure stdlib at runtime, with PyYAML as an optional
extra. It must never import from `kairix` or `tc-agent-zone` — it is the shared
core both depend on.
