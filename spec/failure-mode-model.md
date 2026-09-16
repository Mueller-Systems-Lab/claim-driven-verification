# The failure-mode model

## Why it comes first

> **NO EVIDENCE DESIGN BEFORE FAILURE-MODE ANALYSIS**

This is the one ordering constraint the engine enforces mechanically, and it is
worth being precise about why.

Evidence chosen first and justified afterwards is not verification, it is
confirmation. The designer already believes the feature works; asking "what
evidence would show this?" produces evidence that shows it. Asking "how could
this be wrong?" first produces a different list, because it is a different
question.

The mechanical enforcement is simple: every piece of evidence must bind to a
failure mode, either explicitly through `failure_modes` or implicitly because its
oracle is that failure mode's oracle. Evidence that maps to no failure mode
cannot contribute to a critical claim (`EVIDENCE_NO_FAILURE_MODE`). You cannot
get credit for an observation you never predicted the need for.

## The canonical failure-mode list

Consider, where applicable:

| Failure mode | The question it asks |
| --- | --- |
| false positive | does it report success when the thing did not happen? |
| false negative | does it report failure when the thing did happen? |
| partial success | did some of it happen, and is that being reported as all of it? |
| wrong artifact | is this a valid artifact, but not the requested one? |
| wrong state | is the system in a state it was not asked to reach? |
| stale state | is this the previous result, presented as the new one? |
| correlated failure | did both checks fail together because they share a cause? |
| **wrong observation context** | **was the verifier watching the right target at all?** |
| temporal failure | did it work once, and only once? |
| environmental failure | does it work only in the environment where it was tested? |
| perceptual failure | does it look right without being right? |
| user-outcome failure | is it technically correct but useless to the user? |

This is a prompt for thinking, not a checklist to fill in. A claim with two
sharp failure modes and two matching oracles is in better shape than one with
eleven vague entries, and the engine does not reward quantity.

## Wrong observation context

This one is different from the others, and it is listed separately because of
where it lands. Every other failure mode is a way for the *system under test* to
mislead. Wrong observation context is a way for the *verification* to mislead, and
it is the most dangerous of them, because it corrupts the results rather than
producing a result you can question.

It has a signature that makes it easy to miss:

> The checks pass because the thing being checked never happened in the place
> being inspected.

"File was not created" is true. "No new commit" is true. The commit count is
unchanged. Every observation is accurate, and all of them are about the wrong
directory. A verification that is watching the wrong target reports *everything*
as blocked or as unchanged, which is indistinguishable from a system that is
working perfectly and a guard that is working perfectly.

It is also not hypothetical. It happened: see
`regression-v1.0.0-false-pass.md`. Five checks reported success against a system
that had never been tested.

### The rule

> A verification result MUST NOT be interpreted until the verifier proves that it
> is observing the intended target, execution context, and enforcement path.

Absence of an effect is only evidence of a cause if you were watching the right
place. So context is proven first, and the proof must not be assemblable from
indicators that inherit stale state. Practically, the engine requires an agreeing
indicator from a class produced by somebody other than the party asserting the
context:

| Indicator | Independent? | Why |
| --- | --- | --- |
| runtime's reported project | yes | the runtime says where it actually was |
| per-run marker nonce | yes | planted by the caller, read through an absolute path |
| guard audit log entries | yes | written by a different process into the target |
| `cwd` | no | inherited on process spawn |
| `PWD` | no | inherited, and a runtime may prefer it over the real cwd |
| `git -C <path> rev-parse` | no | restates the path it was given |
| guard at its discovered path | — | position matters: a nested plugin never loads, silently |

Three indicators must agree, at least one of them independent, and **any**
contradiction is fatal. A `PWD` that disagrees with the real working directory is
a failure, not a warning, because a runtime resolving the project from `PWD` will
proceed in the wrong directory while every subsequent check looks correct.

### Where this is checked

`cdv context-proof`, the `tests/lib/context_canaries.py` suite, and a
non-negotiable gate at the top of the end-to-end canary: if context cannot be
proven, the canary aborts **before** reading any behavioural result. It does not
report those results as passing, and it does not report them as failing — it
declines to interpret them, which is the only honest option.

## Structure

```yaml
failure_modes:
  - id: NO_FILE_CREATED
    oracle: filesystem-readback
    description: the export reported success but no file exists
  - id: INVALID_PDF
    oracle: external-pdf-parser
    description: a file exists but is truncated or structurally invalid
```

| Field | Required | Meaning |
| --- | --- | --- |
| `id` | yes | Stable identity. Duplicates within a claim are an error. |
| `oracle` | yes for critical | The mechanism that decides whether this failure mode occurred. |
| `description` | no | What the failure would look like. |

`FAILURE_MODE_WITHOUT_ORACLE` fires when a failure mode has nothing deciding it.
A failure mode without an oracle is a worry, not a check, and worries do not
discharge evidence requirements.

## Coverage

A failure mode is **covered** when at least one evidence path that is passing and
current binds to it.

| Rule | Reason |
| --- | --- |
| A failing path does not cover anything | The gate's purpose is to stop here, not to be satisfied by a red check. |
| A stale path does not cover anything | Evidence about a different state is not weak evidence, it is not evidence. |
| An accepted stale-reuse path covers a non-critical claim | Explicitly declared, attributed reuse is a documented decision. |
| Coverage does not require the failure mode's declared oracle to have produced the evidence | Two independently qualified oracles may both be able to detect the same failure, and independent detection of the same failure is exactly what the architecture wants. The declared oracle must still exist and be qualified. |

Anything uncovered produces `FAILURE_MODE_UNCOVERED`, which blocks the claim.
This is what makes the claim's failure-mode list load-bearing rather than
decorative: the list defines the bars the claim must clear.

## The relationship to criticality

Failure modes are required for critical claims. A `NON_CRITICAL` claim needs no
failure modes — but a claim that matches a criticality trigger is treated as
critical whatever it says, so in practice the way to have a claim without failure
modes is to have a claim that genuinely touches nothing critical.

## Catching correlated failure

The `correlated failure` row above is the hardest to reason about by inspection,
because correlation is a property of pairs of checks rather than of either check.
It is therefore computed rather than judged: see `independence-model.md` for the
dimension comparison that decides it, and note that `CORRELATED_EVIDENCE` is
reported explicitly whenever a claim has to rely on correlated paths.
