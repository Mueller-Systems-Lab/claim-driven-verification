# Claim-Driven Verification

A reusable bootstrap repository for installing and enforcing a verification
architecture in software projects.

The problem it addresses: a green test suite, a successful compile, a passing UI
automation run, a screenshot, an AI judgement, and an application's own success
message are all routinely treated as proof that work is complete. None of them
is proof by itself. This repository installs a system in which completion
decisions must resolve to **explicit claims**, each supported by evidence whose
independence, freshness and oracle quality are computed rather than asserted —
and in which an unsupported completion is **blocked at the point where it would
become an external fact**.

## If you are an AI agent asked to install this

The intended instruction from a user is approximately:

> Use the verification architecture from this repository.
> Install it completely into the current project.
> Verify the installation with the supplied positive and negative canaries.
> Continue development only after the bootstrap gate passes.

What to do:

```bash
git clone <this-repository-url> /tmp/cdv-bootstrap
/tmp/cdv-bootstrap/installer/install.sh "$PWD"
```

Then read the report it prints. **`VERIFICATION_BOOTSTRAP=PASS` is the only
acceptable result before you continue.** If it says `FAIL`, do not proceed and do
not describe the installation as complete: the report lists which checks failed,
and the installer's own notes explain how to satisfy them. Files being copied
successfully is not a passing installation, and the installer will not tell you
it is.

After a PASS you have:

- `.verification/` — the verification engine, the test suite, the audit log
- `.opencode/plugin/verification-guard.ts` — the runtime guard
- `verification.yaml` — this project's claims, seeded with one working example

Continue development normally. The guard injects the current gate state into your
context, exposes `verification_status` and `verification_gate` tools, and refuses
`git commit`, `git push`, `git tag`, publish, deploy and merge operations while
the gate is not PASS.

## What "verifying the installation" means here

```
CONTEXT CANARIES  15 cases (3 positive, 12 negative), including the
                  permanent reproduction of the v1.0.0 wrong-PWD
                  false PASS: each must fail, naming the indicator
                  it violated
POSITIVE CANARY   a complete, correctly evidenced claim set   -> must PASS
NEGATIVE CANARIES fourteen defect cases built from thirteen
                  distinct single mutations: each differs from
                  the passing baseline by exactly one
                  documented change, and each must FAIL for the
                  specific rule meant to detect it
END TO END        in a real project, with a real runtime:
                    gate FAIL -> a completion action is refused,
                                 and the world is unchanged
                    gate PASS -> the same action is permitted,
                                 and it really lands
```

The end-to-end canary is the one that matters. It does not read the guard's
opinion of itself; it looks at the filesystem and at git history, cross-checked
against the guard's audit log, which is written by the runtime process and read
by a different one.

## The canonical flow

```
REQUIREMENT
  -> COMPLETION CLAIM
  -> CRITICALITY
  -> FAILURE-MODE ANALYSIS      (must precede evidence design)
  -> ORACLE DESIGN
  -> EVIDENCE PATHS
  -> INDEPENDENCE ANALYSIS
  -> META-VERIFICATION          (oracle qualification)
  -> CONFLICT RESOLUTION
  -> TEMPORAL / RECOVERY VERIFICATION
  -> WORLD / USER-OUTCOME VERIFICATION
  -> PASS / FAIL
  -> RESIDUAL UNCERTAINTY
```

## What the gate actually enforces

A **critical** claim passes only when every one of these holds. Each line is a
rule with a stable identifier and a canary that proves it fires.

| Requirement | Rule when violated |
| --- | --- |
| Failure modes are declared | `CRITICAL_CLAIM_WITHOUT_FAILURE_MODES` |
| Wrong observation context is refused (v1.0.1) | `CONTEXT_PROOF_MISSING` / `CONTEXT_CONTRADICTION` |
| Every failure mode has an oracle | `FAILURE_MODE_WITHOUT_ORACLE` |
| Every oracle exists | `ORACLE_UNDEFINED` |
| Every oracle is qualified, known-good **and known-bad** | `ORACLE_NOT_QUALIFIED` |
| Qualification demonstrated detection | `ORACLE_QUALIFICATION_BLOCKED` |
| Every failure mode covered by passing, current evidence | `FAILURE_MODE_UNCOVERED` |
| Evidence binds to a failure mode | `EVIDENCE_NO_FAILURE_MODE` |
| **Two materially independent evidence paths** | `INSUFFICIENT_INDEPENDENT_PATHS` |
| No unresolved evidence conflict | `EVIDENCE_CONFLICT_UNRESOLVED` |
| Conflicts resolved by root cause **and a re-run** | `CONFLICT_RESOLUTION_INCOMPLETE` |
| Evidence is current for the evaluated state | `EVIDENCE_NOT_FINAL_STATE` |
| Evidence records the state it observed | `EVIDENCE_STATE_UNBOUND` |
| Temporal claims have repetition **and** durability anchors | `TEMPORAL_CHECKS_MISSING` |
| World claims are not proven by the producer's own report | `WORLD_VERIFICATION_MISSING` |
| Outcome claims have an external oracle | `USER_OUTCOME_UNVERIFIED` |
| Residual uncertainty is recorded and non-empty | `RESIDUAL_UNCERTAINTY_MISSING` |
| No evidence result is FAIL or BLOCKED | `EVIDENCE_RESULT_NOT_PASS` |
| Criticality was not silently downgraded | `CRITICALITY_REVIEW_INCOMPLETE` |

`PASS` means **sufficiently supported**, never **absolutely proven**. A design
decision rather than a hedge: a verdict claiming certainty it cannot have is one
people learn to distrust, and a distrusted gate gets bypassed.

### Independence is computed, not claimed

Two evidence paths are materially independent only if they differ on **both** the
generation axis (`model`, `implementation`, `runtime`) and the observation axis
(`data_source`, `observation_channel`, `specification_source`). `tool` is recorded
and counts for nothing, because naming two tools differently is the cheapest way
to fake rigour. Grades are qualitative — LOW / MEDIUM / HIGH — with no invented
probability, because no empirical calibration exists to justify a number.

### Packaging claims are verified against the installed target

> SOURCE_TREE_PASS != INSTALLED_PACKAGE_PASS

For any packaging claim, the oracle must execute against the installed target. A
suite that passes in the source tree says nothing about what a target receives, and
this is not hypothetical: `edge_cases.py` was installed into every target while
depending on the repository's `examples/` directory, so it aborted with
`FileNotFoundError` in every target and passed perfectly in the repository.

Every installed file now has a declared role — `RUNTIME_REQUIRED`,
`TARGET_TEST_REQUIRED`, `DOCUMENTATION_REQUIRED` or
`INTENTIONALLY_INSTALLED_DATA` — and anything installed with no role fails the
gate. Components that check the repository's own scripts or run the repository's
installer are source-tree-only and are not installed at all.

### Shell strict-mode reliability is gated

`tests/verify.sh` and `installer/install.sh` run under `set -u`, where an undefined
or out-of-order variable aborts the run part-way through — a class that produced
three defects during development and that `bash -n` cannot see. ShellCheck runs
with `--enable=all` (SC2154 is an optional check and silent otherwise), and because
ShellCheck demonstrably does **not** detect the use-before-assignment variant, a
small deterministic scan covers that. Style-only findings are reported, never
gated.

### Context integrity comes before interpretation

Added in v1.0.1, after a real false PASS. A verification result is invalid until
the verifier establishes that it is observing the intended target, execution
context and enforcement path. `cwd` and `PWD` are both inherited on process spawn
and neither is sufficient on its own — `PWD` is specifically the trap, because a
runtime may resolve its project from it while the real working directory says
something else. At least one agreeing indicator must come from somebody other than
the party asserting the context: the runtime's own reported project, a per-run
marker nonce, or the guard's audit log.

Any contradiction is fatal, and the end-to-end canary **aborts before reading any
behavioural result** if context cannot be proven. It does not report those results
as passing and does not report them as failing; it declines to interpret them.

> Absence of change is not proof of blocking until context integrity is
> established.

The full incident record is [`spec/regression-v1.0.0-false-pass.md`](spec/regression-v1.0.0-false-pass.md).

### Oracle qualification has two modes, and they are not interchangeable

| Mode | Meaning | Requires |
| --- | --- | --- |
| `EXECUTED` | the engine ran both cases and observed the results | commands, plus `--allow-execute-oracles` |
| `DECLARED` | the project asserts both cases were run | a rationale and a stated reason execution is impossible |

`DECLARED` is auditable and far better than nothing, but it is an assertion about
the verifier made by the party that benefits from it passing, so it is marked, it
requires a reason, and it produces an explicit assurance downgrade
(`ORACLE_ASSURANCE_DEGRADED`) that stays visible in the result. A critical claim
may not lean on a `DECLARED` oracle where execution is technically possible, and
an unexecuted `EXECUTED` oracle is refused rather than silently downgraded.

### Criticality cannot be downgraded away

A claim whose own text matches a criticality trigger (money, data integrity,
privacy, security, authentication, persistent state, external side effects,
generated artifacts, release readiness, user-visible correctness, safety,
irreversible operations) is evaluated with the **critical** rule set regardless of
how it labels itself. A review record permits the `NON_CRITICAL` label and is
audited and reported — and the critical rules still apply. Relabelling is an
acknowledgement, never a bypass.

## Enforcement boundary — read this before trusting the gate

Measured on OpenCode 1.18.30. Full detail in
[`spec/enforcement-boundary.md`](spec/enforcement-boundary.md).

| Mechanism | Status | Evidence |
| --- | --- | --- |
| Blocking `git commit`/`push`/`tag`/publish/deploy/merge at gate ≠ PASS | **HARD** | World-state readback: the marker file was absent, the commit count unchanged |
| Blocking agent writes to `.verification/` and to the guard | **HARD** | A live write attempt was refused and the file was unmodified |
| Stamping assistant prose that declares unsupported completion | **ADVISORY** | The text is marked, not retracted |
| Injecting live gate state into the system prompt | Informational | — |
| Shell-command tamper detection | **Heuristic** | Blocks the ordinary routes; a deliberately obfuscated command can evade it |

Not enforceable, and not claimed to be: a human editing `verification.yaml`, git
run outside the agent, or the plugin being disabled. **This is a development-time
control, not a security boundary.**

One limitation deserves stating plainly rather than burying: the check on shell
commands is textual. It was already strengthened once, after a live canary
demonstrated that matching `.verification/` missed `cd .verification && echo x >
VERSION`. The gap is narrowed, not closed. The design goal is not to make escape
impossible — an in-process hook cannot — but to make the ordinary route blocked
and any escape deliberate, auditable and therefore attributable.

## Using it on a real project

Edit `verification.yaml`. Replace the seeded example claim with real ones:

```yaml
version: 1
state:
  commit: "..."      # run: .verification/bin/cdv fingerprint
  tree_hash: "..."

oracles:
  filesystem-readback:
    kind: independent-technical
    qualification:
      mode: executable
      positive_command: python3 tools/check.py --expect good
      negative_command: python3 tools/check.py --expect corrupt

claims:
  - id: PDF-EXPORT-001
    statement: Confirmed user data is exported into a valid PDF file.
    type: ARTIFACT
    criticality: CRITICAL
    producer: report-writer
    failure_modes:
      - id: NO_FILE_CREATED
        oracle: filesystem-readback
      - id: INVALID_PDF
        oracle: external-pdf-parser
    evidence:
      - id: EV-001
        oracle: filesystem-readback
        result: PASS
        failure_modes: [NO_FILE_CREATED]
        dimensions:
          implementation: filesystem-readback
          runtime: posix-shell
          data_source: output-directory
          observation_channel: filesystem
          specification_source: filesystem-semantics
        observed_state: { commit: "...", tree_hash: "..." }
    residual_uncertainty:
      - printer-specific rendering not tested on physical hardware
```

Then:

```bash
.verification/bin/cdv validate          # human report
.verification/bin/cdv summary           # machine key=value
.verification/bin/cdv findings          # one line per finding
.verification/bin/cdv fingerprint       # refresh the recorded state
```

Use the example in [`examples/`](examples/) closest to your project:
`minimal/`, `artifact-producing-app/`, `agent-system/`.

## Repository layout

```
README.md  INSTALL.md  VERSION  CHANGELOG.md  LICENSE
spec/          the contract, and the five models it is built from
schema/        verification.schema.json -- the machine-readable contract
templates/     starter verification.yaml
core/cdv/      the engine: runtime-independent, deterministic
runtime/       runtime adapters (OpenCode guard)
installer/     install.sh
tests/         canaries, edge cases, fault injection, E2E, readback, verify.sh
examples/      worked examples
bin/cdv        repository-local launcher
```

Validate everything, including a second provider:

```bash
./tests/verify.sh --model deepseek/deepseek-flash \
                  --second-model zai-coding-plan/glm-4.7
```

`--second-model` is the runtime-diversity canary. Only providers already
authorised at zero marginal cost are used. If none is reachable the report says
`SECOND_PROVIDER_CANARY=BLOCKED_EXTERNAL_AVAILABILITY` and keeps it as residual
uncertainty rather than claiming a success that did not happen.

## Design principles

**The repository is the executable specification.** The method is not copied
into each target project; the engine is installed and the target holds only its
own claims and evidence.

**Fail closed.** Unknown keys, unknown claim types, missing state, unpermitted
oracle execution, an internal engine error — all block PASS. Silence is not
neutral in a verification system, it is permissive.

**No self-attestation.** Independence, freshness and oracle qualification are
derived from recorded facts. An agent cannot assert its way past a gate.

**No fake precision.** Qualitative grades, no fabricated probabilities.

**No paperwork for its own sake.** For every piece of evidence, the question is
*what failure mode does this actually detect?* Evidence that detects nothing
identified earns no credit, and the engine says so.

```
MORE TESTS        !=  MORE INDEPENDENT EVIDENCE
MORE GREEN CHECKS !=  STRONGER COMPLETION PROOF
```

**Conventional testing is not replaced.** Build, lint, types, unit, integration,
UI, E2E, visual, mutation, fuzzing, differential, property-based, fault
injection, recovery, device and user validation all remain. They stop being
self-interpreting and become evidence paths under specific claims.

## Versioning

Semantic versioning; `VERSION` file; schema version tracked separately and
independently (currently schema `v1`). The installer records the bootstrap
version and a hash of every installed file in
`.verification/manifest.json`, which is what makes independent readback possible.

`FINAL_CLASSIFICATION` is read from the `CLASSIFICATION` file, which is the single
source of truth for both the installer and `tests/verify.sh` — two copies of a
label is how two reports come to disagree. The label is release-scoped
(`V1_0_1_HARDENING_VERIFIED`), and the static checks fail if it stops naming the
release in `VERSION`, so a version bump cannot silently carry a stale
classification. A publication run reports its own, higher classification
describing the publication; `CLASSIFICATION` describes the repository's state.

Future consumers should reference **a release tag** rather than mutable `main`.

## What this does not do

It does not detect a lie that is internally consistent — no mechanism can. It
does not make escape from the guard impossible. It does not replace engineering
judgement about what to claim or which failure modes matter.

What it does is make an unsupported completion declaration harder to produce,
harder to hide, and impossible to produce *by accident* — and make the limits of
every claim you do make visible in writing.
