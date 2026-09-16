# Changelog

All notable changes to the Claim-Driven Verification bootstrap repository.

Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Versioning: [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

The schema version of `verification.yaml` is tracked independently of the
repository version. A release that changes the schema says so explicitly and
ships a migration.

## [Unreleased]

Nothing yet.

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
