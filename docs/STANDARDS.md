# tc-fitness engine standards

> **Canonical org index:** [`tc-pipelines/governance/STANDARDS.md`](https://github.com/three-cubes/tc-pipelines/blob/main/governance/STANDARDS.md).
> This file states the standards the tc-fitness **engine** enforces, in the repo
> that ships the enforcing checks. Do not fork a parallel standard: an entry here
> is the source text to **promote up** into the canonical index. The Deterministic
> tests entry below is queued for that promotion + a tc-pipelines CI leg as the
> fast-follow to SGO-200.

---

## Deterministic tests — no `--reruns`, flakes are a must-fix work-item

**Status:** the dynamic half — `core:deterministic_tests` — ships here (SGO-200).
Its static companion `core:no_test_reruns` (the config-level retry ban) is
delivered by the PLA-312 loop governor; the two are designed to agree.
**Origin:** SGO-199 (a ~50%-incidence flake sat undetected in a consumer's coverage
gate) → converged into the engine by SGO-200. Tied to the loop determinism
guardrail (PLA-312 loop governor).

### The standard

1. **Tests are deterministic.** A test's verdict must not depend on execution
   order, wall-clock time, unseeded randomness, network, or state leaked from
   another test. The same commit must produce the same result on every run and
   in any order.

2. **No retry-into-green.** `pytest-rerunfailures`, `--reruns`, and CI
   retry-on-failure actions are **banned**. A retry masks the exact flake this
   standard exists to surface, so "green on the second attempt" is not a pass —
   it is an undetected defect. This is the same rule the loop state machine
   enforces at runtime (it refuses `--reruns`).

3. **A detected flake is a work-item, not noise.** When a test is found to be
   non-deterministic it gets a **must-fix Linear work-item** and, if it cannot be
   fixed immediately, is **explicitly quarantined against that ticket** and
   time-boxed — never left silently retried or skipped. "No work without a work
   item" applies to flakes.

### How the three layers agree (spec ↔ gate ↔ runtime)

| Layer | Mechanism | What it guarantees |
| --- | --- | --- |
| **Spec** | this entry | deterministic tests; retries banned; flakes are must-fix work-items |
| **Gate (static)** | `core:no_test_reruns` (PLA-312) | a repo's pytest/CI config may not *ask* for retries |
| **Gate (dynamic)** | `core:deterministic_tests` | the suite is *actually* stable — run under a fixed seed twice and under shuffled orders; any verdict that differs FAILS, naming the offending test id |
| **Runtime** | loop governor (PLA-312) | the autonomous loop refuses `--reruns` and escalates a repeatedly-red issue instead of retrying it into green |

`no_test_reruns` proves the config never asks to hide a flake; `deterministic_tests`
proves there is no flake to hide. Neither ever enables a rerun — and
`deterministic_tests` additionally blocks the retry plugin inside its own probe
(`-p no:rerunfailures`) so a stray `addopts = "--reruns=N"` cannot mask a flake
mid-run.

### Adopting `core:deterministic_tests`

Add the catalogue row and a config block naming your changed-scope test roots:

```python
# in your catalogue.py
RuleEntry(
    id="deterministic-tests",
    gate="deterministic-tests",
    check="core:deterministic_tests",
    category="test-integrity",
    summary="Test outcomes are stable across a fixed seed (run twice) and shuffled orders.",
)
```

```toml
# in pyproject.toml [tool.tc_fitness]
[tool.tc_fitness.core_checks.deterministic_tests]
roots = ["tests"]        # the (changed-scope) test roots to probe
seed = 0                 # pinned PYTHONHASHSEED — held constant across all runs
repeats = 2              # fixed-seed repeat runs (catches pure flakes)
order_seeds = [1, 2]     # shuffled-order runs (catches order-dependence)
timeout_seconds = 900
# use_randomly = true    # opt in to pytest-randomly for ordering (requires the plugin)
```

With no config block the check is a vacuous pass (the standard adoption
contract) — bind the block to make it bite. It varies order itself with core
pytest, so `pytest-randomly` is optional; set `use_randomly = true` to delegate
ordering to the plugin where a repo has adopted it.

### Fast-follow

* Promote this entry into `tc-pipelines/governance/STANDARDS.md` (the canonical index).
* Add a tc-pipelines reusable CI leg that runs `core:deterministic_tests` over the
  changed-scope suite on PRs, and auto-opens the must-fix work-item on a detected flake.
