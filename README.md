# three-cubes-fitness

> **Part of the [Three Cubes Golden Path](https://github.com/three-cubes)** — the paved road every repo's quality gate, CI, and deploy derive from.
> Sibling: **[tc-pipelines](https://github.com/three-cubes/tc-pipelines)** — the reusable CI workflows that *run* this gate (`uses: …/python-quality-gate.yml@v1`) plus the Azure-VM deploy workflows.

Shared architecture-fitness primitives **and the single runnable quality gate**
for Three Cubes repositories (`kairix`, `tc-agent-zone`). This package is the
**single source** for the helper code those repos' fitness-function checks
previously maintained as two parallel, slowly-drifting copies. Consuming it means
a fix or a behaviour change lands once, not twice.

## The single runnable gate (v0.5.0): `tc-fitness run`

> **The engine owns the gate MACHINERY; the consumer owns the gate CONTENT.**
> This is the load-bearing boundary. The engine knows how to run an ordered list
> of steps and aggregate one verdict. It does **not** know, and must never bake
> in, *which* tests a repo runs, with which `--cov` roots, which ruff/bandit
> targets, which detect-secrets baseline, or where the repo's check-catalogue
> lives. Every one of those is **config** — a step the consumer declares in its
> own `[tool.tc_fitness]` block.

`tc-fitness run` is the one binary both CI and local invoke, so **`local == CI`
by construction** — there is no hand-copied pytest/lint block to drift between a
`scripts/ci/check.sh` and a CI workflow:

```bash
uv run tc-fitness run         # local: this is what `make check` becomes
```

```yaml
# CI: the reusable python-quality-gate.yml shrinks to
#   checkout → setup-uv → uv run tc-fitness run
```

The consumer declares the gate **once**, in a `[tool.tc_fitness]` block in its
`pyproject.toml` (or a dedicated `.tc-fitness.toml`):

```toml
[tool.tc_fitness]
name = "tc-agent-zone quality gate"

[[tool.tc_fitness.steps]]
id = "deps"
run = ["uv", "sync", "--all-packages", "--locked"]

[[tool.tc_fitness.steps]]
id = "ruff"
run = ["ruff", "check", "scripts", "tests"]
fix = "run `ruff check --fix scripts tests`"
next = "re-run tc-fitness run"

[[tool.tc_fitness.steps]]
id = "bandit"
run = ["bandit", "-r", "scripts", "-ll", "-ii", "-c", "pyproject.toml"]

[[tool.tc_fitness.steps]]
id = "tests"
# A `shell` step is run through the shell — use it for the exact pytest line,
# with its test dirs, `--cov` roots, markers, and `-n auto`.
shell = "pytest -q tests -m 'not soak' -n auto --cov=scripts --cov=tools --cov-branch --cov-report=xml:coverage.xml"

[[tool.tc_fitness.steps]]
id = "secrets"
shell = "git diff --name-only origin/main...HEAD | xargs -r detect-secrets-hook --baseline .secrets.baseline"

# The fitness catalogue is dispatched IN-PROCESS via the shared runner —
# no second python boot. It names the consumer's RuleEntry catalogue.
[[tool.tc_fitness.steps]]
id = "fitness"
summary = "architecture fitness functions"
catalogue = "scripts.checks._rule_catalogue:ALL_ENTRIES"
checks_dir = "scripts/checks"
dispatch = "subprocess"
parallel = true
```

Each step is one of:

| Step kind | Field | Runs as |
|---|---|---|
| command vector | `run = ["prog", "arg"]` | a child process (no shell) |
| shell string | `shell = "a \| b"` | a child process through the shell (pipelines / globs / `$(...)`) |
| catalogue | `catalogue = "module:attr"` | the consumer's `RuleEntry` catalogue, dispatched in-process via `tc_fitness.runner.main_cli` |

Per-step options: `summary`, `cwd`, `env`, `allow_missing` (skip when the program
isn't on PATH instead of failing), `continue_on_error` (record a FAIL but don't
gate the aggregate — informational steps), and `fix:` / `next:` lines printed
under the step's FAIL. The full schema lives in
[`src/tc_fitness/gate_config.py`](src/tc_fitness/gate_config.py).

`tc-fitness run` flags: `--repo-root` (default CWD), `--only ID` (run a subset of
steps, repeatable), `--gate ID` (target one fitness rule inside a catalogue step),
`--staged` (the `<60s` smoke tier — catalogue steps run through the *sound*
per-rule `--staged` selection and any step flagged `skip_when_staged` in config,
e.g. a full `pytest`/`mypy` leg, is dropped). This is the fast-feedback
entrypoint kairix's `safe-commit.sh --check` builds on.

## Library modules

It also ships these modules (the helpers `tc-fitness run` and a consumer's checks
both build on):

- **`tc_fitness.lib`** — the merged check helpers:
  - **baseline gating** (from kairix `scripts/checks/_arch_lib.py`):
    `gate()`, `python_files()`, `main_entry()`, `repo_relative()`, `REPO_ROOT`.
  - **agent-actionable emit / YAML** (from tc-agent-zone `scripts/checks/_lib/`):
    `actionable()`, `emit_failures()`, `emit_pass()`, `load_yaml()`, `missing_keys()`.
- **`tc_fitness.ratchet`** — the unified ratchet grammar: one override
  min-length, one marker parser, one suppression grammar (see
  *Drift reconciliation* below).
- **`tc_fitness.runner`** *(v0.3.0)* — the catalogue-driven, repo-agnostic check
  **runner**: in-process dispatch for python checks + guarded (optionally
  parallel) subprocess dispatch for shell checks, the named verdict ledger,
  `--all` / `--gate` / `--staged` modes, and the thin-consumer `main_cli` /
  programmatic `run` API. Supported by `tc_fitness.catalogue` (the `RuleEntry`
  schema), `tc_fitness.context` (the shared file-index + AST parse/walk cache),
  and `tc_fitness.staged` (the sound per-rule staged selection). See *The
  runner (v0.3.0)* below.

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
  "three-cubes-fitness @ git+https://github.com/three-cubes/tc-fitness.git@v0.2.0",
]
```

or, equivalently, on the command line:

```bash
pip install "three-cubes-fitness @ git+https://github.com/three-cubes/tc-fitness.git@v0.2.0"
```

Always pin a tag, never `@main` — the version is the contract the gates depend on.
Because v0.2.0 is an additive superset, a consumer already pinned to `@v0.1.0`
keeps working unchanged; bump to `@v0.2.0` only when you need the new surface.

## The runner (v0.3.0)

v0.3.0 adds a **single, common, repo-agnostic check runner** that both kairix
and tc-agent-zone point their `run_checks.py` at — the structural keystone of
"one common fitness process for all repos". It is purely additive: the v0.1.0 /
v0.2.0 lib + ratchet surface is untouched.

### Thin-consumer API

A consumer repo declares its own `tuple[RuleEntry, ...]` catalogue and its check
modules, then its `run_checks.py` collapses to:

```python
from tc_fitness.runner import main_cli
from .catalogue import RULES
raise SystemExit(main_cli(RULES))
```

`main_cli` parses `--all` / `--staged` / `--gate <id>` and returns the process
exit code. For tests and embedding there is a programmatic
`run(rules, *, mode, staged_files=None, repo_root=None, ...) -> Verdicts`.

### What the runner does

- **In-process dispatch** for python checks (`check_<x>.py` exposing
  `main() -> int`): the module is imported and `main()` is called inside one
  process, sharing a single `CheckContext` whose AST cache parses every file at
  most once. A check that raises is isolated into a FAIL — one crash never
  aborts the ledger.
- **Guarded subprocess dispatch** for `*.sh` shell detectors. Sequential by
  default (byte-identical interleaving with kairix's runner); pass
  `parallel_subprocess=True` to run them on a `ThreadPoolExecutor` with output
  buffered and replayed in catalogue order (tc-agent-zone's parallelism).
- **The named verdict ledger** — a `run [id]` line and a `PASS [id]` / `FAIL
  [id]` verdict per rule, then the aggregate verdict; the format kairix's F83
  gate-runner contract depends on.
- **`--all`** (dispatchable AND `run_all`), **`--gate <id>`** (one rule), and
  **`--staged`** — the *sound* per-rule staged selection (file-local /
  relational / always-run), single-sourced on each `RuleEntry`. The hard
  invariant is **no false negative on a staged change**: when scope can't be
  resolved, the rule runs (fail-safe).
- A **paved-road footer hook** so a failing rule can point an agent at the
  consumer's own query surface.

### Repo-agnostic by injection

The runner never imports `kairix` or `tc-agent-zone`. Repo-specific behaviour is
injected through `RunnerConfig` seams:

| Seam | Purpose |
|---|---|
| `repo_root` / `checks_dir` | where the repo + its check scripts live |
| `scope_resolver` | derive a rule's staged scope from its check script (the repo's FitnessRule-aware hook) when `staged_scope` is unset |
| `enumeration_narrower` | the repo's extra file-index narrowing for file-local staged runs, layered on top of the package-level `tc_fitness.python_files` narrowing |
| `conditional_check` | govern a `subprocess_arg_env` rule's runtime arg + exact skip text (e.g. a coverage check that needs a Cobertura XML) |
| `paved_road_footer` | the affordance line printed under a FAIL |
| `parallel_subprocess` | run shell checks on a thread pool |

`RuleEntry.id` is id-agnostic — it accepts kairix's `"F26"` and tc-agent-zone's
`"no-duplicate-string"` style equally; the runner only uses it as a ledger label
and the `--gate` selector. `category` / `scope` are open `str` fields each repo
curates its own closed vocabulary for.

### Drop-in for kairix's local runner

The runner is byte-identical to kairix's current `scripts/checks/run_checks.py`:
wiring kairix's `_check_context` / `_staged_selection` / `_rule_catalogue` into
`main_cli` via the seams above reproduces the **same verdicts and the same
named-ledger text** for `--all`, `--gate`, and `--staged` (verified by diffing
the two runners' output over the full catalogue, including file-local staged
narrowing). kairix's migration is therefore mechanical: translate its `RuleEntry`
rows to the package schema, pass its three helper modules as hooks, and collapse
`run_checks.py` to the three-line form.

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
