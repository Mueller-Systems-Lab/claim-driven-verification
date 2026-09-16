# The verification contract

This document defines what the Claim-Driven Verification system requires. It is
runtime-independent: nothing here mentions OpenCode, a particular agent, or a
particular language. Runtime-specific enforcement is described in
`enforcement-boundary.md` and implemented in `runtime/`.

## The central object

The unit of verification is the **completion claim**: an explicit, falsifiable
statement about the system that must be true before work is called done.

None of the following is, by itself, a completion claim or evidence for one:

| Not sufficient alone | Why |
| --- | --- |
| A green test suite | Tests assert what the author already believed. A suite that never exercised the failure mode is green and silent. |
| Successful compilation | Compilation is about types, not about behaviour or effect. |
| Successful UI automation | Automation drives the interface; it does not establish that the result outside the application is correct. |
| A screenshot | An artifact of a rendering; it shows pixels, not causality. |
| An AI judgement | An opinion produced by machinery that may share failure causes with the thing it judges. |
| The application's own success message | The producer reporting on itself. This is the single most common source of false completion. |

Each of those *is* useful — see "Relationship to conventional testing" below.
What none of them is, is sufficient by itself.

## The canonical flow

```
REQUIREMENT
  -> COMPLETION CLAIM
  -> CRITICALITY
  -> FAILURE-MODE ANALYSIS        (must precede evidence design)
  -> ORACLE DESIGN
  -> EVIDENCE PATHS
  -> INDEPENDENCE ANALYSIS
  -> META-VERIFICATION            (oracle qualification)
  -> CONFLICT RESOLUTION
  -> TEMPORAL / RECOVERY VERIFICATION
  -> WORLD / USER-OUTCOME VERIFICATION
  -> PASS / FAIL
  -> RESIDUAL UNCERTAINTY
```

The ordering is load-bearing in exactly one place, and it is enforced:
**failure-mode analysis must precede evidence design.** Evidence chosen first and
justified afterwards tends to confirm the design that was already written. The
engine enforces this by requiring that every piece of evidence binds to a failure
mode; evidence that detects no identified failure mode cannot discharge a claim
(`EVIDENCE_NO_FAILURE_MODE`).

## Rules

### 1. Claims are mandatory

Every completion decision resolves to explicit claims. Claims carry a `type`
(`STATE`, `BEHAVIOR`, `ARTIFACT`, `TEMPORAL`, `OUTCOME`), which is a guidance
vocabulary rather than a closed taxonomy — but an unrecognised value is rejected
rather than guessed at, because guessing would silently drop obligations.

### 2. Failure modes come before evidence

See above. The engine reports `CRITICAL_CLAIM_WITHOUT_FAILURE_MODES` when a
critical claim declares none, and `FAILURE_MODE_WITHOUT_ORACLE` when a failure
mode has nothing deciding whether it occurred.

### 3. Every critical failure mode has an explicit oracle

An oracle is the mechanism that actually determines pass or fail. The system
records it, rather than leaving "we looked at it" implicit.

### 4. Two materially independent evidence paths

A critical claim requires at least two evidence paths whose material failure
causes differ. Independence is computed from recorded provenance dimensions, not
declared by the author, and not counted by number of tools. See
`independence-model.md`.

A documented exception may waive this. The exception must be justified,
attributed and dated; it is recorded as residual uncertainty and reported as a
warning. It waives nothing else.

### 5. Criticality defaults conservatively

A claim whose own text matches a criticality trigger is evaluated with the
critical rule set **regardless of how it labels itself**. Relabelling a claim
`NON_CRITICAL` therefore cannot reduce the evidence bar. A downgrade is permitted
only with a review record, and even then the critical rules still apply — so the
reclassification is an acknowledgement, never a bypass.

Areas treated as critical: money, data integrity, privacy, security,
authentication, persistent state, external side effects, generated artifacts,
release readiness, user-visible correctness, safety, and destructive or
irreversible operations.

### 6. PASS means "sufficiently supported"

A critical claim becomes PASS only when all of the following hold:

1. every critical failure mode has sufficient, current evidence
2. every required oracle exists and is qualified
3. the required independent evidence paths exist
4. no unresolved evidence conflict exists
5. all required evidence is current for the final evaluated state
6. required temporal, world and user-outcome checks are satisfied
7. residual uncertainty is recorded

PASS does **not** mean absolutely proven. It means the evidence is adequate for
the claim's criticality, and the limits of that evidence are written down.

### 7. Residual uncertainty is mandatory and non-empty

Every critical PASS retains explicit residual uncertainty. An empty field is
rejected. `NONE_KNOWN` is accepted only with a justification — an unjustified
"NONE_KNOWN" is the same as an empty field with extra steps
(`RESIDUAL_UNCERTAINTY_NOT_JUSTIFIED`).

### 8. Evidence conflicts block the claim; majority voting is never used

When relevant evidence paths disagree, the claim does not pass. The required
sequence is:

```
CONFLICT -> ROOT-CAUSE ANALYSIS -> CORRECTION -> RE-RUN -> NEW EVIDENCE -> RESOLUTION
```

The disagreeing evidence stays in the document so the conflict remains traceable;
it is marked superseded rather than deleted. A resolution must name exactly the
two evidence paths that disagreed, state a root cause and a correction, and
reference re-run evidence that actually passes.

### 9. A one-time success does not prove a temporal claim

Temporal claims require both a repetition anchor (`repeated-execution`, `retry`,
`interruption`) and a durability anchor (`cold-start`, `process-restart`,
`application-restart`, `recovery`, `persistence-boundary`, `state-rehydration`).

### 10. The producer may not be the sole proof of its own external effect

When software claims to change something outside itself, at least one passing
evidence path must come from an oracle that observes the world rather than the
software's account of itself. `internal` oracles and oracles implemented by the
declared `producer` are recorded but never load-bearing.

### 11. Technical correctness does not establish the user outcome

`OUTCOME` claims additionally require a `cross-modal` or `external-reality`
oracle, because a technically correct artifact the user cannot actually use is
not the outcome that was requested.

### 12. Verifiers of verifiers are themselves verified

Every critical oracle must be qualified with a known-good **and a known-bad**
case. The known-bad case is the load-bearing half: an oracle that has never been
shown to reject a deliberately incorrect result is not evidence. Qualification
may be `declared`, or `executable` — in which case the engine runs the supplied
commands and reads the marker from their output, refusing to qualify an oracle
that does not report detection, or that crashes instead of reporting.

Executable qualification is opt-in (`--allow-execute-oracles`), because running
commands from a project's configuration file is a capability that should be
granted deliberately. Without that grant, an executable oracle is reported
unverified and blocks the critical claim rather than being trusted by default.

### 13. Evidence freshness is simple and auditable

The evaluated state is recorded once, at the top level (`state.commit`,
`state.tree_hash`). Each evidence path records what it observed
(`observed_state`). Evidence is current when they agree. Evidence that recorded
nothing is unbound and cannot carry a critical claim. Stale evidence is refused
for critical claims outright; for non-critical claims it is accepted only with a
justified, attributed reuse declaration.

No speculative dependency graph is built. The model is deliberately:

```
FINAL STATE -> FINAL VERIFICATION -> FINAL EVIDENCE
```

### 14. The repository is the executable specification

The bootstrap repository is the canonical source of the method and the engine.
A target repository is the canonical source of its own claims, failure modes,
oracles, evidence references, residual uncertainty and current state. Method and
state are never duplicated; there is one mutable truth per fact.

## Relationship to conventional testing

This system does not replace build, lint, static analysis, type checking, unit
tests, integration tests, UI tests, E2E tests, visual tests, screenshot
inspection, external artifact validation, database or API readback, device
checks, mutation testing, fuzzing, differential testing, property-based testing,
fault injection, recovery tests or user validation.

It consumes them. Each becomes an evidence path supporting a specific claim,
tagged with `evidence_type` and with the provenance dimensions that determine how
independent it is. The change is not that conventional testing stops, it is that
its output stops being self-interpreting.

Two consequences worth stating plainly, because they are the failure modes this
architecture exists to prevent:

```
MORE TESTS        !=  MORE INDEPENDENT EVIDENCE
MORE GREEN CHECKS !=  STRONGER COMPLETION PROOF
```

Prefer the smallest evidence set that covers the real failure modes with
materially different failure causes. For every proposed piece of evidence, ask:
*what failure mode does this actually detect?* If none can be named, the
evidence is decoration.
