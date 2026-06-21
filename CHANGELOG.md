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

## [v0.6.1] — CORE-check config injection (the v0.6.0 checks become consumable)

Wires the documented `[tool.tc_fitness.core_checks.<module>]` config through the
dispatch path so a bound `core:` entry **actually scans the configured tree and
gates on real violations**. In v0.6.0 a consumer could add a
`RuleEntry(check="core:no_duplicate_string")` row, but the runner imported the
module and called its `main()` with NO config — so the rule ran with its
repo-neutral class defaults (`roots=()` → zero files enumerated → a vacuous
pass), and a `dispatch="subprocess"` repo hit a hard "check script not found"
on the non-existent `tc_fitness/core_checks/<module>.py` path. The CORE checks
were inert. This release makes them consumable.

Purely additive over v0.6.0: every existing `runner` / `gate` / `gate_config` /
`core_checks` signature is unchanged (the new behaviour rides new keyword
arguments with safe defaults), so a consumer that binds no `core:` row — or is
pinned to `@v0.6.0` / `@v0.5.0` — is unaffected.

### Added

- **`tc_fitness.gate_config.load_core_check_configs` / `parse_core_check_configs`**
  — read the `[tool.tc_fitness.core_checks.<module>]` sub-tables (from the same
  `.tc-fitness.toml` / `pyproject.toml` source the gate uses) into a
  `dict[module_name, config_block]`.
- **`RunnerConfig.core_check_configs` + `RunnerConfig.establish_baseline`**, and
  matching keyword arguments on `runner.run(...)` and `runner.main_cli(...)`
  (plus a `--establish-baseline` flag on the runner CLI). A `core:<module>`
  entry is now dispatched IN-PROCESS with its config injected via the module's
  `build(config, repo_root=...)`, running `rule.run()` (gate vs the baseline) or
  `rule.establish_baseline()` (adoption mode).
- **`tc-fitness run --establish-baseline`** — the gate threads the flag into the
  catalogue step so a consumer can freeze today's offenders for its bound CORE
  checks with the configured roots.

### Changed

- A `core:` catalogue entry ALWAYS dispatches in-process, regardless of the
  catalogue step's `dispatch` mode — it resolves to an importable
  `tc_fitness.core_checks.<module>` with config injected, never to a script on
  the consumer's checks dir. This closes the `dispatch="subprocess"`
  "check script not found" failure for CORE checks.
- `gate._run_catalogue_step` loads the consumer's CORE-check config blocks and
  passes them (and the `--establish-baseline` flag) through to `main_cli`.

## [v0.6.0] — the canonical CORE check set (FitnessRule ABC + keystone drift-enders)

Promotes the shared fitness machinery so every repo **INHERITS** the canonical
checks via its catalogue instead of reimplementing them. The engine now ships
the `FitnessRule` ABC, the per-file baseline I/O (with an `--establish-baseline`
adoption mode), and the three keystone drift-enders that turn every per-file
baseline into a one-way ratchet. The first CORE check (`no_duplicate_string`)
ships as the copy-pattern subsequent CORE checks follow.

Purely additive over v0.5.0: every existing `runner` / `catalogue` / `lib` /
`staged` / `context` / `gate` / `gate_config` signature is unchanged, and the
new CORE surface is opt-in — a consumer binds a CORE check from its catalogue
only when it repins to `@v0.6.0`. A consumer pinned to `@v0.5.0` / `@v0.4.1` is
unaffected.

### Added

- **`tc_fitness.fitness_rule.FitnessRule`** — the repo-AGNOSTIC, config-driven
  ABC. A concrete CORE check sets `name` + `remediation` + one
  `file_has_violation(path)` method; loading the per-file baseline, enumerating
  in-scope files, applying the scope predicate, and gating on NET-NEW violations
  vs the baseline are inherited. Every repo-specific knob (`roots`,
  `extensions`, `exempt_files`, `name`) arrives through `FitnessRule.from_config`
  from the consumer's `[tool.tc_fitness]` entry — no repo identity is baked in.
- **`tc_fitness.baseline`** — the canonical per-file baseline I/O:
  `.architecture/baseline/<name>-files.txt` (one canonical `-files.txt` suffix),
  `load_baseline` / `establish_baseline` / `render_baseline` / `parse_baseline_text`.
  The `establish_baseline` mode writes today's offenders (with a mandatory
  leading comment block carrying the SHRINK-ONLY contract) so adopting a new
  rule never breaks the build.
- **`tc_fitness.keystone`** — the three drift-enders, all config-driven:
  `net_new_violations_forbidden` (an added file may not appear in any baseline),
  `baseline_shrink_only` (baselines may only shrink across a release boundary),
  and `catalogue_check_consistency` (every catalogued entry ↔ a real check,
  bidirectional).
- **`tc_fitness.core_checks`** — the CORE-check-module convention + the shared
  `run_core_check` `main()` body (parses `--establish-baseline` / `--repo-root`).
- **`tc_fitness.core_checks.no_duplicate_string`** — the first CORE check (Sonar
  S1192) and the exemplar copy-pattern: a `FitnessRule` subclass + `build()`
  factory + `main()`, with the `min_length` / `min_occurrences` thresholds read
  from config.
- **34 promoted CORE checks** — the canonical fitness check set every repo would
  otherwise reimplement, each ported from the kairix F-series and/or
  tc-agent-zone `scripts/checks/` and re-expressed as a repo-AGNOSTIC,
  config-driven `FitnessRule` subclass (its `roots` / `extensions` /
  `exempt_files` / thresholds arrive via the consumer's `[tool.tc_fitness]`
  entry; the engine modules bake in zero repo identity). Each binds from a
  consumer's catalogue via a `RuleEntry(check="core:<module>")` row:
  - _Maintainability / suppressions_ — `cognitive_complexity` (S3776),
    `no_commented_out_code` (S125), `unused_params_named` (S1172),
    `empty_body_intent` (S1186), `no_production_suppressions`,
    `suppressions_have_rationale`, `sonar_ignore_rationale`,
    `shellcheck_disable_with_reason`, `ci_silencers_have_rationale`.
  - _Security / freshness_ — `no_real_names`, `no_logging_secrets`,
    `no_internal_patches`, `no_internal_patches_ts`, `license_present`,
    `adr_number_unique`.
  - _Test discipline_ — `test_skip_rationale`, `every_test_has_tier_marker`,
    `no_test_only_kwargs`, `no_env_monkeypatch`, `no_internal_monkeypatch`,
    `no_test_imports_in_prod`, `no_noop_test_scripts`, `script_help_smoke`.
  - _Naming / paths / docs_ — `posix_path_serialisation`,
    `no_hardcoded_repo_paths`, `path_naming`,
    `no_language_suffix_in_package_names`, `readme_resolver_coverage`,
    `actionable_feedback`, `ci_fanin_parity`.
  - _Ratchets_ — `coverage_floor`, `coverage_includes_branches`,
    `schema_conformance`, `mutation_survival_ratchet`.
- **`tc_fitness.core_checks.CORE_CHECKS`** — the engine's single registry of
  shippable CORE checks in the `core:<module>` namespace, plus
  `discover_core_check_modules()` (the on-disk enumerator) and
  `core_check_consistency()` (gates the registry ↔ modules bidirectionally via
  the `catalogue_check_consistency` keystone, so the engine can never silently
  drift from the checks it claims to ship).
- **`core:` catalogue dispatch in the runner** — `is_core_check` /
  `core_module_name` resolve a `RuleEntry(check="core:<module>")` to
  `tc_fitness.core_checks.<module>` and always dispatch it in-process. A v0.5.0
  consumer that binds no `core:` row is unaffected.

### Deferred to v0.6.1

The CORE promotion deliberately leaves checks whose shape does not fit the
v0.6.0 per-file-baseline `FitnessRule` ABC (a violation keyed on a single file
path) for a v0.6.1 that adds the missing base classes:

- A **keyed-rule base** over `gate_keys` (ID-keyed, `-ids.txt` baseline, stale
  handling) for the inventory-coupled `operator_outcome_tests` (F30) and
  `interface_has_contract_test` (FEAT-150 G1).
- A **command-freshness base** (run a generator/tool, fail on dirty output) for
  `secret_baseline_fresh` and `generated_artifact_fresh`.
- A **consistency-pair / set-reconciliation shape** for
  `required_github_secrets_declared` and `manifest_consistency`.
- A **before/after RatchetRule** (API delta + override grammar) for
  `sonar_quality_ratchet`, and a route-matcher engine primitive for
  `toolpack_route_tests_pass`.

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
