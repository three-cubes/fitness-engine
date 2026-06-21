# Changelog

All notable changes to `three-cubes-fitness` (import `tc_fitness`) are recorded
here. The format follows [Keep a Changelog](https://keepachangelog.com/), and
the project uses [CalVer-free SemVer](https://semver.org/): each `vX.Y.Z` is an
immutable git tag consumers pin in their `pyproject.toml`. **Every release is
additive over the prior one** — existing public signatures stay byte-identical,
new surface is opt-in with safe defaults, so a consumer repins on its own
schedule.

The package is the single source for the helper + runner code kairix and
tc-agent-zone previously maintained as two slowly-drifting copies. It is pure
stdlib at runtime (PyYAML is an optional `yaml` extra) and must never import
`kairix` or `tc-agent-zone` — it is the shared core both depend on.

## [Unreleased]

## [v0.5.0] — `tc-fitness run`, the single runnable gate (EPIC #499 common-process)

Adds the **single runnable quality gate both CI and local invoke** — the binary
that makes `local == CI` true *by construction* instead of by hand-syncing two
copies of a pytest/lint block. A consumer declares its gate ONCE in a
`[tool.tc_fitness]` block; `tc-fitness run` reads that declaration and runs every
step in order, aggregating one verdict. CI's reusable `python-quality-gate.yml`
shrinks to `checkout → setup-uv → uv run tc-fitness run`; a repo's `make check`
becomes `uv run tc-fitness run`.

Purely additive over v0.4.1: every existing `lib` / `ratchet` / `runner` /
`staged` / `catalogue` / `context` signature is unchanged, no behaviour of the
existing surface moves, and the new gate surface is opt-in (a consumer that never
declares a `[tool.tc_fitness]` block is unaffected). A consumer pinned to
`@v0.4.1` keeps working unmodified; repin to `@v0.5.0` to adopt the gate.

### Added

- **`tc-fitness` console script** (`[project.scripts] tc-fitness =
  "tc_fitness.gate:main"`) — invoked as `uv run tc-fitness run` / `uvx
  tc-fitness run`. The single entrypoint both surfaces shell out to.
- **`tc_fitness.gate`** — the step ORCHESTRATOR. Runs the declared steps in
  config order, each as a `run` (argv) / `shell` (string) / `catalogue`
  (in-process via `tc_fitness.runner.main_cli`) action; prints a named
  `run [id]` / `PASS` / `FAIL` / `SKIP` ledger per step + an aggregate verdict;
  returns non-zero iff a gating step failed. `--only ID` runs a step subset;
  `--gate ID` targets one fitness rule inside the catalogue step;
  `continue_on_error` records a non-gating FAIL; `allow_missing` skips a step
  whose program isn't on PATH. Public API: `run_gate`, `GateOutcome`,
  `StepResult`, `main`.
- **`tc_fitness.gate_config`** — the declarative `[tool.tc_fitness]` schema +
  loader. Resolves `.tc-fitness.toml` (whole-document config) over a
  `[tool.tc_fitness]` block in `pyproject.toml`; validates each step (exactly one
  of `run` / `shell` / `catalogue`; unique ids; `module:attr` catalogue refs;
  the `inprocess` / `subprocess` dispatch vocabulary) and raises an
  agent-actionable `GateConfigError` (carrying `fix:` / `next:`) on a
  misconfiguration. Public API: `GateConfig`, `StepSpec`, `GateConfigError`,
  `find_config_file`, `parse_config`, `load_config`.
- **The engine/consumer boundary is enforced by design** — nothing in the engine
  hard-codes a consumer's pytest scope, `--cov` roots, ruff/bandit targets,
  detect-secrets baseline, or check-catalogue path. They are all CONFIG (declared
  steps), so the engine never recreates the caller-parameter coupling a reusable
  workflow with `pytest-args` / `cov-roots` inputs would.

### Changed

- **License `Proprietary` → `Apache-2.0`** — corrects an incoherent
  proprietary marker on a public repo. A `LICENSE` file is now shipped and
  `license-files` references it (PEP 639). No code or signature change.

### Repo hygiene (engine self-CI)

- A repo-self-CI workflow (`.github/workflows/ci.yml`) now runs `pytest` +
  `ruff check` + `mypy --strict` on every push / PR — the engine eats its own
  dog food. A pinned `[tool.ruff]` + `[tool.mypy]` config makes both green over
  the package (the runner's subprocess dispatch + type-narrowing asserts carry
  scoped per-file ignores; the optional PyYAML import is marked
  `ignore_missing_imports`).

## [v0.4.1] — byte-stable `--staged` output (EPIC #499 common-process)

Makes `--staged` / staged-dispatch output **byte-stable**, matching the quality
of the `--all` path. Purely additive over v0.4.0: no public signature changes,
and the staged SELECTION + PASS/FAIL set are **unchanged** — only the output
FORMAT/stability changes. Consumers (kairix, tc-agent-zone) repin and their
`--staged` output simply stabilises; no verdict moves.

### Fixed

- **Staged subprocess dispatch now CAPTURES + replays child output in catalogue
  order, like `--all`.** Previously `_dispatch_staged` routed `.sh` / conditional
  subprocess checks through the non-capturing sequential `_run_one_subprocess`,
  which ran the child with stdout inherited on fd1. Under output redirection (a
  pre-commit / CI pipe, or any `--staged > file.log`) the child's direct-fd
  stdout raced the parent's buffered `print()` ledger — interleaving lines, or
  vanishing from a captured buffer entirely — so the report was not byte-stable.
  Staged subprocess checks now run via the SAME capturing path the `--all`
  parallel dispatch uses: the child's stdout/stderr are captured into pipes the
  parent owns and replayed in catalogue order BETWEEN the rule's `run [id]` and
  `PASS`/`FAIL [id]` lines. No fd race, no interleave, consistent per-rule
  framing. The capturing primitive (`_capture_one_subprocess`) and the named
  ledger replay (`_replay_subprocess_verdict`) are now shared by the `--all`
  parallel path and the staged path, so both emit the identical subprocess
  framing.

### v0.4.0 — declarative seam absorption (EPIC #499 common-process)

Absorbs the consumer-side injection seams kairix and tc-agent-zone hand-code
into declarative engine config, so both repos become *pure consumers*. Purely
additive over v0.3.0: every existing `runner` / `staged` / `catalogue` / `lib`
signature is unchanged, the three callable seams (`scope_resolver`,
`enumeration_narrower`, `conditional_check`) are still accepted, and the new
factories / fields / flags are opt-in with safe defaults.

The model stays **shared machinery, per-repo domain**: every factory keeps the
repo's attribute names / ABC types / fallback roots / skip text as *config
arguments* — the engine never bakes `"RULE"`, `"kairix"`, or any consumer's ABC
into a default.

#### Added

- **`tc_fitness.staged.make_module_roots_resolver(*, boundary_rule_attr=None,
  roots_attr="roots", abc_type=None, abc_roots_attr=None,
  location_marker=None, fallback_roots=None, checks_dir_on_path=True)`** — a
  declarative `ScopeResolver` factory generalising kairix's
  `_kairix_scope_resolver` / `_roots_from_module`. Derives a check module's
  staged scan roots from (1) a module-level boundary-rule attribute carrying a
  `roots` tuple, (2) an ABC subclass's `roots` class attribute, (3) an optional
  location-marker fallback, else `fallback_roots`. All attribute/class names are
  config — nothing kairix-specific is baked in: `boundary_rule_attr` defaults to
  `None` (the boundary-rule branch is OFF unless configured), so kairix's
  `"RULE"` convention is not privileged as the engine default — kairix passes
  `boundary_rule_attr="RULE"` explicitly.
- **`tc_fitness.staged.make_binding_narrower(*, extra_method=None)`** — a
  declarative `EnumerationNarrower` factory generalising the repo-agnostic half
  of kairix's `_kairix_enumeration_narrower`: narrows every already-imported
  `check_*` module's by-value `python_files` binding to the staged set,
  restoring on exit. The package-level `tc_fitness.python_files` is the runner's
  job (its `_run_staged_one` wraps `restrict_python_files` around the narrower),
  so the factory adds NO redundant internal restrict — it narrows only the
  by-value surfaces. It discovers the genuine ORIGINAL binding from the check
  modules themselves (not the package attribute, which the outer restrict has
  already rebound under composition), so the per-check narrowing fires through
  the real runner. The one kairix-specific residue — patch *this* ABC's
  `enumerate_files` — is the optional `extra_method=(SomeClass, "enumerate_files")`
  argument.
- **`tc_fitness.runner.make_env_path_conditional_check(*, env_var, default_rel,
  repo_root, force_skip=None, force_skip_lines=(), absent_skip_lines=(),
  force_skip_line_fn=None, absent_skip_line_fn=None)`** — a declarative
  `ConditionalCheck` factory generalising kairix's `_make_conditional_check`:
  resolves a runtime-arg path from an env var (else a repo-relative default),
  returns a `ConditionalResult` that runs with the path appended, or skips with
  the consumer's exact skip lines when forced (`--skip-coverage`-style) or
  absent. The `*_skip_line_fn` callables receive the `RuleEntry` so two rules
  SHARING one script and differing only by `entry.id` (kairix's F7/F9, both
  `check_per_file_coverage.py`) emit DISTINCT `skip [F7]` / `skip [F9]` ledgers
  instead of one static tuple's identical text — the byte-identity contract for
  shared-script rules. The fn wins over the static tuple; the tuple stays for
  the single-rule case. New `SkipLineFn` type alias.
- **`main_cli(..., extra_flags=(), post_parse=None)`** — `extra_flags` adds
  consumer-specific argparse flags (e.g. kairix's `--skip-coverage`); `post_parse`
  maps the parsed `Namespace` to extra `run()` kwargs (e.g. a
  `conditional_check` built from the flag), retiring the consumer's forked
  `main()`.
- **`RuleEntry.script_path_override`, `RuleEntry.static_extra_args`,
  `RuleEntry.env_gated_extra_args`** — declarative fields for taz's hand-coded
  argv exceptions: a script resolved *outside* the checks dir, always-appended
  args, and args appended only when their env var is set. Wired into subprocess
  argv assembly; default-safe.
- **Public subprocess-dispatch mode** — `run(..., dispatch="subprocess")` /
  `main_cli(..., dispatch="subprocess")` routes every check through the guarded
  subprocess path (replacing taz's reimplemented dispatch). The genuinely-shared
  ledger primitives are promoted to public API: `print_aggregate`, `select_all`,
  `select_gate`, and the `Colours` namespace. The underscore aliases
  (`_print_aggregate`, `_select_all`, `_select_gate`) remain as thin
  back-compat re-exports until taz migrates.
- **`gate(..., fail_on_stale=False, stale_remediation=None)` +
  `gate_keys(..., fail_on_stale=False, stale_remediation=None)`** — opt-in
  stale-baseline detection: a baseline entry no longer present in the current
  scan FAILs (the consumer supplies the remediation text); on pass the banner
  reports new-vs-grandfathered counts. The default (`False`) preserves the
  v0.1.0 exit-code contract byte-identically.
- **`tc_fitness.checks.branch_naming`** — a configurable engine gate lifting
  taz's Linear `gitBranchName` (`<user>/<team>-<number>-<slug>`) branch-name
  check, with `exempt_branches` / `exempt_patterns` as constructor args so each
  repo extends the exempt set (taz keeps `develop`; kairix doesn't).

## [0.3.0] — catalogue-driven, repo-agnostic check runner

Added a single, common, repo-agnostic check **runner** that both kairix and
tc-agent-zone point their `run_checks.py` at — the structural keystone of "one
common fitness process for all repos". Purely additive over v0.2.0.

### Added

- **`tc_fitness.runner`** — in-process dispatch for python checks
  (`check_<x>.py` exposing `main() -> int`, imported and called inside one
  process sharing a single `CheckContext` AST cache; a crashing check is
  isolated into a FAIL) + guarded, optionally-parallel subprocess dispatch for
  `*.sh` shell detectors; the named verdict ledger (`run [id]` / `PASS [id]` /
  `FAIL [id]` + aggregate); `--all` / `--gate <id>` / `--staged` modes; the
  thin-consumer `main_cli` and the programmatic `run(rules, *, mode, ...) ->
  Verdicts`.
- **`tc_fitness.catalogue`** — the repo-agnostic `RuleEntry` schema (id-agnostic:
  accepts kairix's `"F26"` and taz's `"no-duplicate-string"` equally; open
  `category` / `scope` vocabularies).
- **`tc_fitness.context`** — the shared `CheckContext` (file index + AST
  parse/walk cache; parse-once invariant).
- **`tc_fitness.staged`** — the sound per-rule staged selection (`file-local` /
  `relational` / `always-run`) with injectable scope derivation; the hard
  invariant is no false negative on a staged change (fail-safe run when scope
  can't be resolved).
- Repo-agnostic by injection — never imports a consumer. Repo specifics
  (`scope_resolver`, `enumeration_narrower`, `conditional_check`,
  `paved_road_footer`, `parallel_subprocess`) are `RunnerConfig` seams. Verified
  byte-identical to kairix's pre-migration local runner over the full catalogue.

## [0.2.0] — additive surface for tc-agent-zone

Extended the lib + ratchet surface to cover tc-agent-zone's check fleet,
additively, so kairix's `@v0.1.0` pin needed no change.

### Added

- `actionable(what, fix, nxt, run=None)` — the optional third `run:` marker
  yielding the 3-marker form taz's fix/next/run checks emit; the 2-marker
  default stays byte-identical.
- `remediation(fix, nxt, run, *, passing=None, forbidden=None)` — the F21-shape
  multiline remediation block (action markers + optional Pass / Forbidden
  examples).
- `gate_keys(name, current, remediation, *, baseline_suffix="-ids.txt")` — the
  string-keyed sibling of `gate()` for baselines keyed on a logical id
  (`-ids.txt`) or a path-glob (`-paths.txt`) rather than a working-tree path.
- `min_len` keyword on `is_vague_reason` / `parse_overrides` (default 40) so
  taz's 10-char shell-directive floor is a per-call choice, never a mutation of
  the shared default.

## [0.1.0] — merged fitness lib + reconciled ratchet

Initial release: the merged shared core, unioning two independently-grown
libraries into one source.

### Added

- **`tc_fitness.lib`** — baseline-gating helpers from kairix's `_arch_lib.py`
  (`gate`, `python_files`, `main_entry`, `repo_relative`, `REPO_ROOT`) +
  agent-actionable emit / YAML helpers from tc-agent-zone's `_lib/`
  (`actionable`, `emit_failures`, `emit_pass`, `load_yaml`, `missing_keys`).
- **`tc_fitness.ratchet`** — the unified ratchet grammar, resolving three
  drift points to one behaviour each: override-rationale minimum length → 40
  chars (strictly-less-than is vague); the suppression-pattern superset (one
  grammar, `NOSONAR` included); the override-marker separator accepting both an
  em-dash and an ASCII hyphen.
