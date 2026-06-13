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
    gate, python_files, main_entry, repo_relative, REPO_ROOT,
    # agent-actionable emit / YAML (tc-agent-zone surface)
    actionable, emit_failures, emit_pass, load_yaml, missing_keys,
    # unified ratchet primitives
    OVERRIDE_MIN_REASON_LEN, make_override_re, parse_overrides, Override,
    COVERAGE_OVERRIDE_RE, MUTATION_OVERRIDE_RE,
    is_vague_reason, VAGUE_OVERRIDE_RE,
    SUPPRESSION_PATTERNS, BARE_SUPPRESSION_PATTERNS,
    contains_suppression, is_bare_suppression,
)
```

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

## How repositories consume it

Pin to a tag in your `pyproject.toml` (git install — no PyPI publish):

```toml
[project.optional-dependencies]
dev = [
  "three-cubes-fitness @ git+https://github.com/three-cubes/fitness-engine.git@v0.1.0",
]
```

or, equivalently, on the command line:

```bash
pip install "three-cubes-fitness @ git+https://github.com/three-cubes/fitness-engine.git@v0.1.0"
```

Always pin a tag, never `@main` — the version is the contract the gates depend on.

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
