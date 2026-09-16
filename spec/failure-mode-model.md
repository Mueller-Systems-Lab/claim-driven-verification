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
| temporal failure | did it work once, and only once? |
| environmental failure | does it work only in the environment where it was tested? |
| perceptual failure | does it look right without being right? |
| user-outcome failure | is it technically correct but useless to the user? |

This is a prompt for thinking, not a checklist to fill in. A claim with two
sharp failure modes and two matching oracles is in better shape than one with
eleven vague entries, and the engine does not reward quantity.

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
