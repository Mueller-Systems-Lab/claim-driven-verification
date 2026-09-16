# Worked examples

Three documents, each complete and each passing the gate. They exist so that a
new project can copy the closest one rather than derive the structure from the
specification.

These examples are **tested**. `tests/lib/edge_cases.py` section D validates every
one of them against the engine, so a future engine change that made these
documents invalid would fail the test suite rather than leave the documentation
quietly lying. The `commit` and `tree_hash` values are fixed sentinels so the
examples are self-contained and hermetic; a real project gets real values from
`.verification/bin/cdv fingerprint`.

| Example | Copy it when |
| --- | --- |
| [`minimal/`](minimal/verification.yaml) | you are starting out and have nothing critical yet |
| [`artifact-producing-app/`](artifact-producing-app/verification.yaml) | your software produces a file, document, image or export that something else consumes |
| [`agent-system/`](agent-system/verification.yaml) | your software is a system that runs, restarts, and is judged by whether a user can accomplish a task |

## What to notice in each

### `minimal/`

The smallest honest document. One non-critical claim, two independent evidence
paths, residual uncertainty recorded. Its value is showing that the scaffolding is
not the point: a claim, two observations, and a written note about what was not
checked.

### `artifact-producing-app/`

The reference shape for anything that emits a file. Three things repay attention:

- **The failure modes come before the evidence.** `NO_FILE_CREATED` with a
  filesystem oracle, then `INVALID_PDF` with a parser oracle. Evidence designed
  against an identified failure is different evidence from evidence designed to
  look convincing.
- **The oracles are qualified**, including the known-bad case that is the only
  thing showing they can distinguish correct from incorrect.
- **The two evidence paths differ on both axes.** Different implementation,
  different runtime, and different observation channel and reference. Two reads
  of the same file through two wrappers would be one observation with two names.

`producer: report-writer` is what stops the application's own success message
from counting as proof of its external effect.

### `agent-system/`

The shape for a system whose correctness is not a single artifact. It combines:

- `type: OUTCOME` — the claim is about what the user can do, not about what the
  code produced
- `requires_temporal: true` — the claim must survive a restart, so it needs both
  a repetition anchor (`repeated-execution`) and a durability anchor
  (`process-restart`)
- an `external-reality` / `cross-modal` oracle, because an agent that reports its
  own success is not evidence of its own success

The `residual_uncertainty` entries are worth copying in spirit: they name what
was *not* checked. An empty field is rejected by the engine, and `NONE_KNOWN`
needs a justification, so the temptation to write nothing is removed.

## Adapting one

1. Run `.verification/bin/cdv fingerprint` and paste the result into `state`.
2. Replace the claims with your own. Write the statement so it could be false.
3. For each claim, ask how it could be true-looking but false. Those are the
   failure modes.
4. For each failure mode, name the thing that decides it. That is the oracle.
   Qualify it with a case you know is bad.
5. Only now decide what evidence to collect.
6. Write down what you did not check.
7. Run `.verification/bin/cdv validate` and read the blocking findings.

If a claim fails and you cannot say which failure mode the missing evidence would
have detected, the claim is probably not yet stated in falsifiable terms.
