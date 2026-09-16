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

### Declared qualification

```yaml
qualification:
  mode: declared
  positive_case:
    description: the real artifact is present
    result: PASS
  negative_case:
    description: the artifact was removed; the oracle must notice
    result: DETECTED
```

The project asserts that both cases were run. This is auditable and cheap, but it
is an assertion, so the report records that the oracle was qualified in
`declared` mode — the residual weakness stays visible rather than being rounded
away.

### Executable qualification

```yaml
qualification:
  mode: executable
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

### Execution is opt-in

Running commands taken from a project's configuration file is a real capability,
so it is granted deliberately rather than assumed. Without
`--allow-execute-oracles`, an oracle declaring `mode: executable` is reported
`ORACLE_QUALIFICATION_UNVERIFIED` and **blocks the critical claim**.

That is the fail-closed direction on purpose. The alternative — silently treating
an unexecuted oracle as qualified — would mean the strongest form of
qualification was the one you could get without doing anything.

Note the comparison: refusing to run is *not* the same as refusing to qualify.
`declared` mode remains available to projects that cannot or will not run oracle
commands, and the report states which mode was used for every critical oracle.

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
