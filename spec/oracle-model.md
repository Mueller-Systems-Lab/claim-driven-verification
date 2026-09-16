# The oracle model

## What an oracle is

An oracle is the thing that actually decides pass or fail. Not the person, not
the agent, not the reviewer — the mechanism.

The system requires this to be recorded explicitly, because "we looked at it and
it seemed fine" is the most common implicit oracle in software work, and it is
not an oracle at all. It is an opinion with no fixed decision rule, which means
it cannot be re-run, cannot be qualified, and cannot disagree with itself
visibly.

## Kinds

| Kind | Stands for | Can be load-bearing for a world claim? |
| --- | --- | --- |
| `internal` | the producing system's own account of itself | no |
| `independent-technical` | a different technical mechanism | yes |
| `cross-modal` | a different observational modality (e.g. visual) | yes |
| `external-reality` | the world outside the software | yes |

`internal` is not worthless — it is cheap, and it catches a real class of
problem. It simply may never be the *sole* proof of a claim about the world,
because the software's own report cannot falsify the software's own report.

For `OUTCOME` claims, at least one passing path must come through a `cross-modal`
or `external-reality` oracle, because technical correctness does not establish
that the user can achieve what they asked for.

## Oracle qualification — verifying the verifier

Every oracle carrying a critical claim must itself be qualified, with a
known-good **and a known-bad** case.

> A verifier that has never demonstrated detection of a known bad result must not
> automatically be trusted for critical evidence.

The known-bad case is the load-bearing half. Showing that an oracle accepts a
correct input shows that it can run. Showing that it *rejects* a deliberately
incorrect input is the only thing that shows it can distinguish.

The two modes are named for what happened, not for what was possible, and they
are deliberately **not** interchangeable.

### EXECUTED qualification

```yaml
qualification:
  mode: EXECUTED
  positive_command: python3 tools/check_pdf.py --expect good
  negative_command: python3 tools/check_pdf.py --expect corrupt
```

The engine runs both commands and reads the result from their output. The
contract:

- the positive command must exit 0 and print a line beginning
  `CDV_ORACLE_POSITIVE=PASS`
- the negative command must exit 0 and print a line beginning
  `CDV_ORACLE_NEGATIVE=DETECTED`

Anything else — a non-zero exit, a missing marker, a different value — is a
refusal to qualify. A crash is not a detection: an oracle that fails open, or that
dies instead of reporting, is refused (`ORACLE_QUALIFICATION_BLOCKED`).

This is fault injection against the verification machinery itself, and it is what
the `mutation testing / fault injection / deliberate corruption / malformed
artifacts / stale-state injection / wrong-value injection / missing-effect
injection` requirement means in practice.

### DECLARED qualification

```yaml
qualification:
  mode: DECLARED
  rationale: >
    The oracles run against a live third-party sandbox that is not reachable
    from the CI environment where this document is validated.
  executable_unavailable_because: >
    The sandbox requires credentials the CI job does not hold, so neither the
    known-good nor the known-bad case can be executed here. Tracked in issue 4711;
    re-run manually before each release.
  positive_case:
    description: the real artifact is present
    result: PASS
  negative_case:
    description: the artifact was removed; the oracle must notice
    result: DETECTED
```

The project asserts that both cases were run. This is auditable and cheap, and it
is far better than nothing — but it is an assertion about the verifier made by
the party that benefits from the verifier passing, so it carries three
obligations:

- **It must be marked.** The mode is preserved in the claim's assurance, in the
  JSON, and in `CDV_ORACLE_QUALIFICATION_MODE` in the summary. A DECLARED oracle
  never silently appears equivalent to an EXECUTED one.
- **It must have a reason.** A rationale, and a statement of why executing is not
  possible, each at least 40 characters. The reason has to be written down to be
  reviewable; the engine cannot determine feasibility itself, so it requires the
  project to state it.
- **It must retain residual uncertainty.** A critical claim whose oracles are all
  DECLARED gets `ORACLE_ASSURANCE_DEGRADED` recorded and an explicit residual
  uncertainty entry naming the gap, in addition to whatever the project records
  itself.

### Execution is opt-in

Running commands taken from a project's configuration file is a real capability,
so it is granted deliberately rather than assumed. Without
`--allow-execute-oracles`, an oracle declaring `EXECUTED` is reported
`ORACLE_QUALIFICATION_UNVERIFIED` and **blocks the critical claim**.

Note what it is *not* downgraded to. An unexecuted `EXECUTED` oracle is not
quietly treated as `DECLARED`: that would mean the document received weaker
assurance than it asked for, which is a silent substitution. Refusing is the
fail-closed direction, and `DECLARED` remains available to any project that
chooses it explicitly.

### A critical claim and a declared oracle

> A critical claim must not rely solely on a `DECLARED`-qualified oracle if
> executable qualification is technically feasible.

Three rules implement this from three angles:

| Rule | Fires when |
| --- | --- |
| `ORACLE_DECLARED_MISSING_FEASIBILITY` | DECLARED without stating why execution is impossible |
| `ORACLE_DECLARED_MISSING_RATIONALE` | DECLARED without a stated rationale |
| `ORACLE_EXECUTABLE_FEASIBLE_BUT_DECLARED` | the document admits execution was feasible and declared anyway |

The third is the sharpest: a project that states `executable_feasible: true` has
said that stronger evidence was available and was not produced. That is refused
rather than warned about.

Where a claim has at least one `EXECUTED` oracle, its assurance is `MIXED` or
`EXECUTED`, and the stronger path is doing real work. Where every oracle is
`DECLARED`, the claim can still pass — the architecture does not pretend a
project can always execute its oracles — but the weakness is written into the
verification result and cannot be read past.

## What the engine records

For every oracle a critical claim uses, the report includes its kind, whether it
is qualified, and by which mode. `ORACLE_UNDEFINED` fires when an oracle is
referenced but not defined — a dangling reference is an error rather than a
skipped check, because a skipped check that the document believes exists is worse
than no check at all.

## The mutation requirement is not theoretical

The bootstrap repository's own test suite runs fault injection against its own
oracle-qualification machinery:

- an oracle that never rejects anything must be **refused** qualification
- an oracle that crashes instead of reporting must be **refused** qualification
- an oracle with a sound known-bad case must be **certified**

See `tests/lib/edge_cases.py`, section C. A verification system whose own
verification behaviour is never tested is a verification system with an untested
foundation.
