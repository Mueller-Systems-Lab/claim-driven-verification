# The independence model

The requirement: a critical completion claim needs two **materially independent**
evidence paths. This document defines "materially independent" precisely enough
that a program can decide it, and explains why the definition is shaped the way
it is.

## Two things this model refuses to do

**It does not count tools.** Two paths are not independent because they are named
differently, produced by different programs, or invoked from different scripts.
The cheapest way to fake rigour is to run the same observation twice behind two
wrappers, and a rule that counts tools rewards exactly that.

**It does not take the author's word for it.** Independence is not a field an
agent fills in. It is derived from recorded provenance facts, so an agent that
wants a claim to pass cannot simply assert that its evidence is independent.

## The dimensions

Each evidence path records seven dimensions.

| Dimension | Axis | Meaning |
| --- | --- | --- |
| `model` | generation | Which model produced or judged the result, if any |
| `implementation` | generation | Which mechanism produced the result |
| `runtime` | generation | Which interpreter or platform it ran on |
| `data_source` | observation | Which data the result was read from |
| `observation_channel` | observation | Through which channel it was read |
| `specification_source` | observation | Which reference defines what "correct" means |
| `tool` | audit only | Recorded, never counts |

The **generation axis** answers: *was this produced by the same machinery?* Two
paths that share a model, an implementation and a runtime share their material
failure causes, however differently they are packaged. If the implementation has
a bug, both paths inherit it.

The **observation axis** answers: *did we look at the world through the same
window, against the same reference?* Reading the same file twice through two thin
wrappers is one observation, not two. Two oracles that both trust the same
reference document will both be wrong together if that document is wrong.

`tool` is recorded for audit and deliberately counts for nothing. It is the
dimension that is easiest to vary without changing anything that matters.

## The rule

For a pair of evidence paths, compare all seven dimensions. Let

- `gen` = the generation-axis dimensions that differ
- `obs` = the observation-axis dimensions that differ

Then:

| Condition | Grade | Materially independent? |
| --- | --- | --- |
| `gen` empty and `obs` empty | LOW | no |
| only `gen` non-empty | LOW | no |
| only `obs` non-empty | LOW | no |
| both non-empty, `len(gen)+len(obs) <= 2` | MEDIUM | yes |
| both non-empty, `len(gen)+len(obs) >= 3` | HIGH | yes |

A pair counts toward the requirement only at MEDIUM or above.

The two-axis requirement is the core of the model. Differing only on generation
means one observation of one world state — two independent witnesses who both
looked at the same photograph. Differing only on observation means the same
machinery looked twice — a single mechanism, sampled twice.

## Unset values

A dimension that is absent, `null` or an empty string normalises to a single
sentinel. Consequently:

- two paths that both leave a dimension blank count as **sharing** it
- one path blank and one filled counts as differing

Blank-matches-blank must never manufacture independence. This is the reason a
flat fallback for missing nesting is not provided either: an evidence path with
no `dimensions` mapping has no recorded dimensions, which is reported, rather
than quietly treated as having them.

## Claim-level grade

All pairs among the claim's supporting evidence are assessed. Supporting means
passing **and** current: a failing or stale path cannot be counted as an
independent second witness, because a broken check would otherwise manufacture
rigour.

The claim's grade is the best pair achieved. Correlated pairs are reported as
warnings so the pattern is visible, but they do not by themselves fail a claim
that has a material pair.

## Detecting correlation

The model refuses a pair when it cannot rule out a shared failure cause. The
canonical correlations the two-axis rule catches:

| Correlation | Caught because |
| --- | --- |
| Same model family judging its own output | `model` and `implementation` are shared |
| The same requirement, misinterpreted | `specification_source` is shared |
| The same reference data | `data_source` is shared |
| One implementation behind two interfaces | `implementation` is shared |
| The same simulator | `runtime` and `implementation` are shared |
| The same parser | `implementation` and `observation_channel` are shared |
| The same synthetic source | `data_source` is shared |
| The same hidden assumption | whatever dimension encodes it is shared |

## The canonical principle

> No critical completion claim may be accepted solely on evidence whose material
> failure causes are shared with the generation process.

This is why `internal` oracles can never be load-bearing for a claim about the
world, no matter how many of them agree.

## Why qualitative grades

LOW / MEDIUM / HIGH, with no numeric probability. No empirical calibration
exists to justify a number, and inventing one would be false precision — the
kind of self-assured figure that makes an unverified claim look measured.

When a review needs to know *why* a grade was assigned, the engine reports the
exact dimensions that differed and the sentence explaining the decision. A verdict
that cannot be accounted for is one that gets worked around.

## Exceptions

An independence exception is permitted as the contract requires, but it must be
explicit: claim id, justification of at least 40 characters, `accepted_by` and
`accepted_at`. It is then recorded as residual uncertainty and reported as a
warning. It waives the two-path requirement and nothing else: coverage, oracle
qualification, freshness, conflict resolution and residual uncertainty all still
apply, so an exception cannot be used to pass a claim that has no real evidence.
