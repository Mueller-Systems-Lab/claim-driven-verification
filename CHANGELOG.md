# Changelog

All notable changes to the Claim-Driven Verification bootstrap repository.

Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Versioning: [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

The schema version of `verification.yaml` is tracked independently of the
repository version. A release that changes the schema says so explicitly and
ships a migration.

## [Unreleased]

## [1.0.2] - 2026-09-17

Corrective release. Scope is limited to two demonstrated defect classes: installed
test packaging integrity, and shell strict-mode reliability in the verification
entry points. No architectural change: the engine, the guard, the gate logic and
every v1.0.1 proof are untouched.

### Fixed — installed test packaging

The installer shipped `tests/lib/edge_cases.py` into every target, but section D of
that suite reads the repository's `examples/` directory, which is not installed.
The installed copy therefore aborted with `FileNotFoundError` in every target while
passing perfectly in the source tree. A test that appears runnable and
deterministically fails is worse than no test: it trains the reader to ignore
failures.

Corrected by **not installing source-tree-only components**, rather than by copying
the repository's example documents into every target. `edge_cases.py` validates the
repository's shipped examples, which is a repository-documentation concern, and the
installer already runs the suites that are target concerns. It remains a required
file in the source tree; it is simply not installed.

The same criterion was then applied to the two new gates themselves:
`shell_check.py` (checks the repository's shell entry points) and
`packaging_check.py` (runs the repository's installer) are source-tree-only and are
deliberately **not** installed. Installing either would have been the same defect
again with different names.

This also exposed a third coupling: `independent_readback.py` kept a literal list of
installed-to-source file pairs, so removing a component from the install set made
it report drift for a file that was deliberately no longer there. It now derives its
comparison set from the installation manifest, so the install surface can change
without the check needing to be edited, and an installed file it cannot corroborate
is reported rather than missed.

### Added — packaging contract and gate

- **`tests/lib/packaging_check.py`** implements the packaging contract. Every
  installed file must have a declared role — `RUNTIME_REQUIRED`,
  `TARGET_TEST_REQUIRED`, `DOCUMENTATION_REQUIRED` or
  `INTENTIONALLY_INSTALLED_DATA` — so nothing is shipped by accident and nothing is
  shipped without the resources it needs. Anything installed with no declared role
  fails the gate, which means adding a file to the installer without deciding its
  role fails rather than silently enlarging the installed surface.
- **Fresh-install execution test.** The gate creates a disposable target, installs
  into it, and then executes **every** installed verification entry point in that
  target. It is not satisfied by a source-tree pass.
- **`INSTALLED_TEST_PACKAGING_GATE`**, required for the release.
- **`PACKAGING_NEGATIVE_CANARY=FAIL_AS_DESIGNED`**, a permanent regression that
  re-introduces the original defect in two forms: installed-but-undeclared (caught
  by the role rule) and declared-but-resource-absent (caught by execution, with the
  same `FileNotFoundError` that shipped).

The canonical rule this establishes, now stated in the module: **for a packaging
claim, the oracle must execute against the installed target**, because
`SOURCE_TREE_PASS != INSTALLED_PACKAGE_PASS`.

### Added — strict-mode shell reliability

`tests/verify.sh` and `installer/install.sh` run under `set -u`, where an undefined
or out-of-order variable aborts the run part-way through. That class produced three
defects during development — `VERBOSE`, `SECOND_MODEL`, `SECOND_OK` — and each of
them passed `bash -n`, because `bash -n` parses without evaluating.

- **ShellCheck**, installed through a standard zero-cost package route, is now the
  primary tool, run with **`--enable=all`**. That flag is not optional: SC2154
  ("referenced but not assigned") is an *optional* check and is silent at every
  default severity, which is precisely the class of interest.
- **Measured limit, and the gap it leaves.** ShellCheck detects a variable that is
  never assigned anywhere. It does **not** detect a variable used before it is
  assigned later in the same file — verified directly against ShellCheck 0.11.0 at
  every severity with `--enable=all`. That variant is `SECOND_OK`, the defect that
  actually reached a run. A gate relying on ShellCheck alone would have missed one
  of the three demonstrated defects and reported PASS.
- **`tests/lib/shell_check.py`** therefore adds the smallest deterministic scan for
  the ordering class ShellCheck cannot see, plus unbound references as defence in
  depth. Only top-level references are ordered against assignments: function bodies
  execute when called, not where written, and comparing them textually produces
  false positives on correct code. Defaulted expansions (`${VAR:-x}`) and
  shell-provided or `CDV_`-prefixed names are excluded for the same reason.
- **Findings are triaged.** Only codes that can cause a wrong or aborted run fail
  the gate: SC2154, SC2034, SC2329, SC2086, SC2046, SC2164, SC2181, SC2128, SC2145,
  SC2115, SC2068. The 423 style-only findings `--enable=all` emits (SC2250 "prefer
  braces", SC2292 "prefer `[[ ]]`") are counted and reported but never gated,
  because mechanically rewriting harmless style is not the job.
- **Five real findings fixed:** a `say()` function that was never invoked, and four
  assignments that were written and never read (`PREFLIGHT_OK`, `POSITIVE`,
  `NESTED_OK`, `SECOND_PROVIDER`) — dead residue of the same class.
- **`SHELLCHECK_GATE`** is reported `NOT_AVAILABLE` rather than `PASS` when
  ShellCheck cannot be found, because a static check that did not run has not
  verified anything.
- **`SHELL_UNBOUND_NEGATIVE_CANARY=FAIL_AS_DESIGNED`**, a permanent regression
  covering both variants, and verified non-vacuously by introducing the ordering
  defect into the real `tests/verify.sh` and confirming it is reported with both
  line numbers.

### Note on scope

No new dependencies were added to what a *target* requires. ShellCheck is needed to
run this repository's release gate, not to install or use the verification system
in a project.

### Fixed

- **Contradictory classifications.** The report generators each hardcoded their
  own `FINAL_CLASSIFICATION` label while the release specification named a
  different one, so the same state was reported under two names. There is now one
  canonical classification, read from the `CLASSIFICATION` file by both the
  installer and `tests/verify.sh`, and a static check fails if the label stops
  naming the release in `VERSION` so a version bump cannot carry a stale one.
- **A false PASS in the positive control.** "The permitted commit really landed"
  searched `git log -3` for the canary's commit subject, so the *previous*
  provider's commit in the same target satisfied it. The check is now scoped to a
  change this run caused: HEAD must have moved and the new HEAD must be the
  commit. Also relaxed from "exactly one commit" to "at least one", since the
  property is that the action was permitted, not how many times it ran.
- **External provider unavailability reported as a canary failure.** A
  subscription provider exhausted its weekly quota part-way through a run and the
  second-provider canary reported a failure that had nothing to do with the
  guard. The canary now detects availability refusals and the pipeline reports
  `SECOND_PROVIDER_CANARY=BLOCKED_EXTERNAL_AVAILABILITY`, counted as neither a
  pass nor a product failure and retained as residual uncertainty, as the
  specification requires.

## [1.0.1] - 2026-09-16

Hardening in three areas found by running the v1.0.0 end-to-end canary against a
real runtime. The objective was not "more verification"; it was better trust in
the verification itself.

Schema version stays **1**. Every new document field is optional and additive, and
the v1.0.0 spelling `mode: executable` is still accepted and means `EXECUTED`, so
no document needs to be rewritten to stay readable. One behavioural change does
require attention: see "Migration" below.

### Added — rules

Twelve new rule identifiers (68 total in this release): six for verification
context integrity and six for oracle qualification assurance.

### Added — verification context integrity

A real false PASS occurred in v1.0.0: the canary launched the agent with
`cwd=target` while `PWD` still pointed at the caller's repository, and the runtime
resolved the session from `PWD`. The agent ran elsewhere, and five checks reported
success against a system that had never been tested. The full incident record is
`spec/regression-v1.0.0-false-pass.md`.

- **`WRONG_OBSERVATION_CONTEXT`** added to the canonical failure-mode model. It is
  the one failure mode that lives in the *verifier* rather than in the system
  under test, and it is the most dangerous because its signature is that all the
  checks pass.
- **New canonical rule, verification context integrity.** A verification result is
  invalid unless the verifier establishes that it is observing the intended
  target, execution context and enforcement path. Wrong-context verification fails
  closed, and absence of change is not proof of blocking until context is proven.
- **`core/cdv/context.py`** — context proof as a first-class primitive in the
  engine, with `cdv context-proof`. Indicators are collected independently and
  graded: `cwd` and `PWD` are both inherited on spawn and neither is sufficient;
  `git -C <path> rev-parse` restates its input and is not independent either.
  At least one agreeing indicator must come from a class produced by somebody
  other than the party asserting the context — the runtime's own reported
  project, a per-run marker nonce, or the guard's audit log.
- **`tests/lib/context_canaries.py`** — 15 cases (3 positive, 12 negative)
  covering the wrong-PWD regression, a correct PWD with the process in a different
  repository, an outright wrong target, a stale marker nonce, an inactive
  enforcement path, a guard present but at a non-discoverable path, a guard absent
  entirely, a process outside the expected root, a symlinked path resolving
  elsewhere (and a symlink that correctly resolves to the target, which must still
  be accepted), an inherited-only proof, a missing engine, and an
  unset-but-required `PWD`. Each negative case must fail *naming the indicator it
  violated*, so a failure for an unrelated reason is caught rather than counted.
  A third of the suite is positive on purpose: a check that always failed would
  satisfy every negative case and be worthless.
- **`tests/lib/edge_cases.py`** grew to 29 assertions, adding a section on
  qualification assurance: both modes, both incomplete-DECLARED cases, the
  feasible-but-declared refusal, the EXECUTED masquerade, the v1.0.0 spelling
  still being accepted, an unrecognised mode, and confirmation that a DECLARED
  claim still has to record its own residual uncertainty.
- **The end-to-end canary now aborts before interpreting any behavioural result**
  if context cannot be proven. It does not report those results as passing and it
  does not report them as failing; it declines to interpret them.

### Added — oracle qualification assurance

`EXECUTED` and `DECLARED` are now a first-class distinction rather than an
implementation detail of one code path.

- **`EXECUTED`** means the engine ran both cases and observed the results.
  **`DECLARED`** means the project asserts they were run. The mode is preserved
  in the claim's assurance, in the JSON, and in `CDV_ORACLE_QUALIFICATION_MODE`.
  A DECLARED oracle never silently appears equivalent to an EXECUTED one.
- **A `DECLARED` qualification must now state a rationale and why executing is
  unavailable** (each at least 40 characters), under
  `ORACLE_DECLARED_MISSING_RATIONALE` and `ORACLE_DECLARED_MISSING_FEASIBILITY`. A
  critical claim may not rely on a declared oracle unless executing it is
  genuinely impossible, and that has to be written down.
- **`ORACLE_EXECUTABLE_FEASIBLE_BUT_DECLARED`** refuses a document that admits
  execution was feasible and declared anyway.
- **`ORACLE_EXECUTED_WITHOUT_COMMANDS`** refuses a declared case presented as an
  executed one. Without it, the stronger assurance could be obtained by
  relabelling.
- **`ORACLE_ASSURANCE_DEGRADED`** records, on a critical claim whose oracles are
  all `DECLARED`, that the qualification is an assertion rather than an
  observation, and adds the gap to the claim's residual uncertainty.
- An unexecuted `EXECUTED` oracle is **refused rather than downgraded to
  `DECLARED`**, because silently substituting the weaker mode would give the
  document less assurance than it asked for.

### Added — runtime and provider diversity

- **A second provider canary** (`zai-coding-plan/glm-4.7`, an already-authorised
  subscription model, so no paid external usage was introduced). The guard is
  intended to be model-independent; this runs the full guard flow on a second
  model to show it rather than assert it. `tests/verify.sh` reports
  `SECOND_PROVIDER_CANARY`, and if no second provider is reachable it reports
  `BLOCKED_EXTERNAL_AVAILABILITY` and keeps that as residual uncertainty instead
  of fabricating success.

### Changed

- The end-to-end canary now emits machine-readable verdicts: `TARGET_IDENTITY`,
  `CONTEXT_PROOF`, `GUARD_HOOKS`, `NEGATIVE_ACTION`, `POSITIVE_ACTION`,
  `STATE_EDIT_NOT_BLOCKED`, `STATE_EDIT_CONFIRMED`, `UNDESERVED_PASS_BLOCKED`.
- **Every block in the end-to-end canary is now provoked and the attempt is
  evidenced before its assertion is made.** A check of the form "the file was not
  created" is satisfied both by a guard that blocked the action and by an agent
  that never attempted it, and it cannot tell the two apart. Each action is
  provoked on its own, by up to three different routes, and its assertion is only
  read once the guard's audit log shows a refusal appended *during that step*
  (scoped, because the log persists across providers and runs). If an attempt
  cannot be provoked the property is reported as **UNTESTED, not passing**.
  This was found by running the suite against two models: the second model
  declined to attempt the anti-tamper write in one run, which under the previous
  design would have left the property untested while its "state unmodified" check
  passed vacuously.
- **A model may comply before the guard has to enforce.** Running against a
  second provider showed a model reading the injected gate state and declining to
  attempt a write to `.verification/` at all, on the grounds that it is protected.
  From the outside that is indistinguishable from a guard that blocked the write:
  both leave the file unchanged. The canary therefore requires the guard's audit
  log to record a refusal *appended during that step*, reports
  `ANTI_TAMPER=NOT_PROVOKED_STATE_UNCHANGED` when no attempt could be provoked,
  and `tests/verify.sh` requires `REFUSED_AND_RECORDED` from the primary provider
  so coverage is guaranteed at release level. The same applies to provoking a
  real `git commit`: a model that reads the gate state and declines to commit
  while the gate is failing is behaving as designed. The pipeline therefore splits
  the claim — the *mechanism* is proven per provider by provoking a harmless
  configured completion pattern that goes through the same enforcement path; the
  *policy* (which commands count as completion actions) is verified
  deterministically by `static_checks.py` reading the guard's default list, and
  exercised end to end once by the primary provider. Documented in
  `spec/enforcement-boundary.md` as a limit of what an enforcement test can show:
  model cooperation is a precondition for probing the guard, not for the guard
  working.
- The editability confirmation now asks for a **legitimate** update — adding a
  residual-uncertainty entry, which is the actual workflow. The first version
  asked the agent to append a stray comment, and the agent refused on principle,
  judging that modifying the file would invalidate its recorded state binding.
  That was sound reasoning: testing a guard by asking an agent to do something it
  should decline measures the agent, not the guard.
- The end-to-end canary proves agent editability of `verification.yaml` as a
  property separate from undeserved-PASS prevention. The guard must **not**
  classify the project's verification state as protected infrastructure, and the
  engine must **not** accept a self-serving document. The first is asserted from
  the guard's own audit log, which is deterministic; whether the write actually
  landed is recorded as `STATE_EDIT_CONFIRMED` rather than required, because that
  depends on the agent complying and a false alarm is how a check gets switched
  off.
- `cdv summary` emits `CDV_ORACLE_QUALIFICATION_MODE`.

### Fixed

- **Static checks missed a real defect.** A rule id existed in `errors.py` and was
  used in `gates.py` but never imported into `gates.py`; the existing check passed
  because the name does resolve in `errors.py`, just not in the module using it.
  It surfaced as an `ENGINE_INTERNAL_ERROR` on the one path that reached it. The
  check is now AST-based and proves that every rule-shaped name is bound in its
  own module, and it has been verified to fail when the bug is reintroduced.

### Migration

Schema version is unchanged, and no document field was removed or renamed.
However, **the DECLARED qualification requirements are new, and they apply to
critical claims.** A document whose critical claim relies on `mode: declared`
without `rationale` and `executable_unavailable_because` will now fail with
`ORACLE_DECLARED_MISSING_FEASIBILITY`. Add both fields, or convert the oracle to
`EXECUTED` and run with `--allow-execute-oracles`. The templates and all three
examples were updated this way; the reasons recorded in them are genuine rather
than boilerplate.

Nothing else in the contract changed, and every v1.0.0 rule still applies.

### Not done, deliberately

- `verification.yaml` was **not** made write-protected. Recording claims, failure
  modes, evidence and residual uncertainty is the workflow, not an attack. The
  guard validates changes instead of forbidding them, and the separate property —
  that editing the state cannot buy a PASS — is tested explicitly.
- No speculative shell-command obfuscation detection was added. The existing
  bash tamper check remains a documented heuristic, and the boundary in
  `spec/enforcement-boundary.md` is unchanged: this is a development-time
  control, not a security boundary against a hostile user.

## [1.0.0] - 2026-09-16

First release. The bootstrap repository is created and validated end to end.

### Added

- **Engine** (`core/cdv/`) — runtime-independent, deterministic verification
  engine. Python 3 with PyYAML as its only external dependency. Subcommands:
  `validate`, `summary`, `findings`, `gate`, `rules`, `version`, `fingerprint`,
  `init`. Exit codes 0/1/2/3 for PASS / FAIL / usage / missing prerequisite.
- **Rule set** — 56 stable rule identifiers covering input handling, criticality,
  failure modes, oracles, evidence, independence, conflict, freshness, temporal,
  world and user-outcome verification, and residual uncertainty.
- **Independence model** — material independence computed from recorded
  provenance dimensions, not declared. Two paths must differ on both the
  generation axis (`model`, `implementation`, `runtime`) and the observation axis
  (`data_source`, `observation_channel`, `specification_source`). `tool` is
  recorded and counts for nothing.
- **Criticality model** — conservative defaults with an anti-downgrade rule: a
  trigger-matching claim is evaluated with the critical rule set regardless of its
  label, so reclassification cannot lower the evidence bar.
- **Oracle qualification** — known-good and known-bad cases, in `declared` or
  `executable` mode. Executable qualification is opt-in and runs the project's own
  commands, refusing to qualify an oracle that does not report detection or that
  crashes instead of reporting.
- **Schema** (`schema/verification.schema.json`) — JSON Schema draft 2020-12,
  versioned `$id`, schema `v1`.
- **OpenCode guard** (`runtime/opencode/verification-guard.ts`) — flat plugin,
  auto-discovered. Hard blocks on completion-adjacent tool calls and on agent
  writes to the verification state; advisory stamping of unsupported completion
  prose; system-prompt state injection; three agent-facing tools
  (`verification_status`, `verification_gate`, `verification_refresh_state`);
  audit log with loud-on-failure writes.
- **Installer** (`installer/install.sh`) — idempotent, version-aware,
  non-destructive, fail-closed. Never overwrites `verification.yaml`. Refuses to
  downgrade. Reports `VERIFICATION_BOOTSTRAP=PASS` only after the installed engine
  rejects known-bad input, all five gate dimensions are exercised, a real runtime
  blocks an invalid completion while permitting a valid one, and the installation
  independently reads back as matching its manifest.
- **Test suites**:
  - 15 canaries (`tests/lib/canaries.py`) — one passing baseline plus fourteen
    defect cases built from thirteen distinct single mutations, each asserted to
    fail for the specific rule meant to detect it. Building the negatives as
    single mutations of a passing baseline is what makes the suite meaningful: a
    gate that always failed would satisfy "the defect fails" and be caught by the
    baseline, and a gate that always passed would be caught by the defects.
  - 20 edge cases and positive-path tests (`tests/lib/edge_cases.py`), including
    fault injection against the oracle-qualification machinery and validation of
    every shipped example. The positive-path tests exist because asserting only
    that a waiver blocks when absent would be satisfied by a waiver that never
    works.
  - end-to-end enforcement canary (`tests/e2e/enforcement_canary.py`) — 20 checks
    using world-state readback and the guard's audit log as independent channels,
    with unconditional harness sanity gates
  - independent readback (`tests/independent_readback.py`) — re-hashes the
    installation against its manifest and against the bootstrap source
  - static consistency checks (`tests/lib/static_checks.py`) — every referenced
    rule id resolves, gate dimensions map to real rules, versions agree
  - `tests/verify.sh` — runs all of the above and prints the final report
- **Documentation** — `spec/verification-contract.md` and the five models it is
  built from, plus `spec/enforcement-boundary.md` recording the measured runtime
  behaviour and stating exactly where enforcement is hard, advisory, heuristic,
  and absent.
- **Examples** — `minimal`, `artifact-producing-app`, `agent-system`, each
  validated by the test suite so the documentation cannot drift from the engine.

### Design decisions worth recording

- **Fail-closed on unknown input.** An unrecognised top-level key is an error, not
  a warning. A typo such as `oracle:` for `oracles:` would otherwise be ignored and
  the document would pass with its oracle definitions never read.
- **`NOT_EXERCISED` rather than vacuous PASS.** A gate dimension given nothing to
  check is reported as untested, not as passing.
- **Qualitative independence grades.** LOW / MEDIUM / HIGH with no numeric
  probability, because no empirical calibration exists to justify a number.
- **No tool registration for the completion gate itself.** The gate is enforced on
  tool calls, which is the primitive verified to block. The agent-facing gate tool
  converts "I think I am done" into a recorded, answered question.
- **Audit writes are loud on failure.** An audit trail that silently records
  nothing makes the guard look like it ran when it did not.
- **Evidence-resolution semantics.** A conflict resolved by root cause and re-run
  evidence permits the claim to pass, with the disputed evidence marked
  superseded rather than deleted, so the conflict stays traceable.

### Corrected during development

Recorded because each was found by the system's own tests, and because the
corrections are more useful to a reader than an appearance of having got it right
first time.

- **Criticality over-triggering.** Several trigger tokens were substrings of
  unrelated common words (`ui` in "build", `tax` in "syntax", `token` in
  "tokenizer", `life` in "lifecycle", `print` in "prints"). The token table was
  rewritten so it cannot fire from inside an unrelated word, and evidence
  descriptions were removed from the scanned text entirely: criticality is a
  property of what a claim asserts, not of how it was tested.
- **Resolved conflicts could never pass.** The original rule rejected any non-PASS
  evidence immediately, which made the required
  `CONFLICT -> ROOT CAUSE -> CORRECTION -> RE-RUN -> RESOLUTION` sequence
  impossible to complete. Non-PASS evidence is now judged after conflict
  resolution and marked superseded when a resolved conflict explains it.
- **Coverage required the failure mode's own oracle.** This made it impossible for
  two independently qualified oracles to corroborate one failure mode, which is
  the kind of independent detection the architecture wants. Coverage now accepts
  any evidence that binds to the failure mode.
- **The shell tamper check missed `cd .verification && ... > VERSION`,** because it
  matched only `.verification/` with a trailing slash. A live canary rewrote the
  engine's VERSION file and the check was widened to a separator-or-word-boundary
  match. The remaining limitation is documented rather than hidden.
- **The end-to-end canary reported false passes.** It launched the agent with
  `subprocess(cwd=target)` but `PWD` still pointed at the caller's repository, and
  the runtime resolves the session's project from `PWD`. The agent therefore ran
  in a different repository while the canary inspected the target, so five
  assertions passed vacuously against a system that had not been tested. `PWD` is
  now set explicitly, and the canary aborts before interpreting any result unless
  it can show the agent ran in the target project and the guard's hooks fired.
- **`audit()` swallowed its own failures,** so the guard's audit trail could be
  absent while blocks still fired. Failures are now reported once to stderr.
- **The engine state cache keyed on mtime and size,** which can collide when a
  document is rewritten within the filesystem's timestamp resolution at the same
  length, serving a stale verdict. It now keys on a content hash.
