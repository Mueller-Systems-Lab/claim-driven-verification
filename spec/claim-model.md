# The claim model

## What a completion claim is

A completion claim is an explicit, falsifiable statement about the system that
must be true for work to be called done.

Good:

> Confirmed user data is exported into a valid PDF file.

Bad:

> The export feature is finished.

The bad one cannot be tested, so no evidence can support it, so any evidence
"supporting" it is really supporting something else. The discipline of writing
the claim first is most of the value; the gate is what stops the discipline from
being skipped under time pressure.

## Fields

| Field | Required | Purpose |
| --- | --- | --- |
| `id` | yes | Stable identity. Duplicates are a document-level error, because identity is what every later lookup resolves against. |
| `statement` | yes | The falsifiable statement. |
| `type` | yes | Guidance vocabulary; see below. |
| `criticality` | yes | `CRITICAL` or `NON_CRITICAL`. Must be explicit. |
| `criticality_rationale` | for `NON_CRITICAL` | At least 80 characters. |
| `producer` | for world claims | The component whose self-report must not be the sole proof. |
| `failure_modes` | yes for critical | How the claim could be true-looking but false. |
| `evidence` | yes | The observations, with provenance. |
| `temporal_checks` | for temporal claims | Repetition and durability anchors. |
| `residual_uncertainty` | yes for critical | What the evidence does not cover. |
| `requires_temporal` | optional | Force temporal obligations on a non-`TEMPORAL` claim. |
| `requires_world_verification` | optional | Force external-effect obligations on a non-`OUTCOME` claim. |

## Types

Types are a guidance vocabulary, not a closed taxonomy — the architecture is
explicit that the categories are guidance. But an unrecognised value is *rejected*
rather than accepted silently, because guessing at the category would guesses at
the obligations too.

| Type | Asserts | Extra obligations |
| --- | --- | --- |
| `STATE` | the system is in a particular state | — |
| `BEHAVIOR` | the system does a particular thing | — |
| `ARTIFACT` | a durable artifact is produced | — |
| `TEMPORAL` | the claim holds across time, restart or recovery | repetition + durability anchors |
| `OUTCOME` | the world, or the user's task, actually changed | external oracle, and a cross-modal or external-reality oracle |

`TEMPORAL` and `OUTCOME` obligations can also be requested explicitly on any
claim via `requires_temporal` / `requires_world_verification`. Types are a
shorthand for the obligations, not the only way to acquire them.

## Criticality

Criticality decides how much evidence a claim needs, so it is the most
consequential field in the document, and therefore the most conservatively
handled.

### It defaults to critical

A claim is treated as critical when either:

- it declares `criticality: CRITICAL`, or
- its own text matches a criticality trigger

The second condition is not overridable. This matters because the cheapest
possible attack on a verification system is to relabel an expensive claim as
cheap. Under this rule, relabelling changes nothing about the evidence required.

### Triggers

Matching is case-insensitive substring matching over the claim's **statement**,
id, type, notes, and its failure-mode ids and descriptions.

Evidence descriptions are deliberately *not* scanned. Criticality is a property
of what a claim asserts, not of how it was tested — scanning evidence prose
classified a claim as critical because the word "prints" appeared in a
description of how the evidence was gathered. That is a bug, and the test suite
covers it.

The trigger areas are: money, data integrity, privacy, security,
authentication, persistent state, external side effects, generated artifacts,
release readiness, user-visible correctness, safety, and destructive or
irreversible operations. The full token lists are in
`core/cdv/model.py:CRITICALITY_TRIGGERS`.

Tokens are chosen so they cannot fire from inside an unrelated common word. Bare
fragments such as `ui` (matches "build"), `tax` (matches "syntax"), `token`
(matches "tokenizer") and `life` (matches "lifecycle") are deliberately absent. A
trigger table that fires on everything is not conservative, it is unusable, and an
unusable gate is one that gets switched off. The tokens are still broad enough to
err toward classification as critical, which is the conservative direction.

### Downgrading

To classify a trigger-matching claim as `NON_CRITICAL` you must supply:

- `criticality_rationale` of at least 80 characters, and
- a matching `criticality_exceptions` entry with `justification`, `reviewed_by`
  and `reviewed_at`

With all of that present, the engine emits `CRITICALITY_DOWNGRADE_BLOCKED` as a
**warning**, records the claim as downgraded in the report, and **still evaluates
it with the critical rule set**.

That last point is the whole design. If a review record could actually lower the
evidence bar, then obtaining a review record would become the cheapest way to
pass a claim. Making the exception purely an acknowledgement — visible, audited,
and evidentially inert — means the conservative default cannot be argued away.

So the answer to "can I mark this non-critical to avoid the evidence?" is: you can
mark it, the mark will be recorded, and you will still have to produce the
evidence.

## Claims without claims

A document with no claims is structurally valid and reports
`NOT_EXERCISED` for every gate dimension, never a vacuous PASS. A dimension that
was given nothing to check has not been demonstrated to work.
