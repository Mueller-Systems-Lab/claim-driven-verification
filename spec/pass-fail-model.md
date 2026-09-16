# The pass/fail model

## The verdict

Each claim receives `PASS` or `FAIL`. The document receives `PASS` only when
every claim passes and no document-level error exists.

`PASS` means **sufficiently supported**. It does not mean absolutely proven. The
distinction is not modesty, it is operational: a verdict that claims certainty it
cannot have is a verdict people learn to distrust, and a distrusted gate gets
bypassed.

`FAIL` means at least one blocking finding exists. Blocking findings are those
with severity `ERROR`. Warnings never block; they are recorded so that a
documented compromise stays visible without being fatal.

## What must hold for PASS

For a **critical** claim:

| # | Requirement | Rule when absent |
| --- | --- | --- |
| 1 | declares at least one failure mode | `CRITICAL_CLAIM_WITHOUT_FAILURE_MODES` |
| 2 | every failure mode has an oracle | `FAILURE_MODE_WITHOUT_ORACLE` |
| 3 | every oracle used exists and is defined | `ORACLE_UNDEFINED` |
| 4 | every oracle used is qualified (known-good and known-bad) | `ORACLE_NOT_QUALIFIED` |
| 5 | qualification actually demonstrated detection | `ORACLE_QUALIFICATION_BLOCKED` |
| 6 | every failure mode is covered by passing, current evidence | `FAILURE_MODE_UNCOVERED` |
| 7 | passing evidence is bound to a failure mode | `EVIDENCE_NO_FAILURE_MODE` |
| 8 | at least two materially independent evidence paths | `INSUFFICIENT_INDEPENDENT_PATHS` |
| 9 | no unresolved evidence conflict | `EVIDENCE_CONFLICT_UNRESOLVED` |
| 10 | conflict resolutions are complete and re-run | `CONFLICT_RESOLUTION_INCOMPLETE` |
| 11 | evidence is current for the evaluated state | `EVIDENCE_NOT_FINAL_STATE` |
| 12 | evidence records the state it observed | `EVIDENCE_STATE_UNBOUND` |
| 13 | temporal claims have repetition and durability anchors | `TEMPORAL_CHECKS_MISSING` |
| 14 | world claims have a non-self oracle | `WORLD_VERIFICATION_MISSING` |
| 15 | outcome claims have a cross-modal or external-reality oracle | `USER_OUTCOME_UNVERIFIED` |
| 16 | residual uncertainty is recorded and non-empty | `RESIDUAL_UNCERTAINTY_MISSING` |
| 17 | no evidence result is FAIL or BLOCKED | `EVIDENCE_RESULT_NOT_PASS` |
| 18 | criticality is not silently downgraded | `CRITICALITY_REVIEW_INCOMPLETE` |

For a **non-critical** claim, requirements 1–5 are skipped and requirement 8 is
relaxed to "at least one passing, current evidence path". Requirements 6, 7, 9–12,
17 and 18 still apply. Requirement 16 does not apply.

Note requirement 18: a claim that matches a criticality trigger *is* critical for
the purposes of this table, whatever its label (see `claim-model.md`). So the
column you read is decided by the claim's content, not by its author's
classification.

## Document-level errors

These fail the whole gate regardless of individual claims:

| Rule | Condition |
| --- | --- |
| `INPUT_FILE_MISSING` | no verification document found |
| `YAML_MALFORMED` | the document is not valid YAML |
| `ROOT_NOT_MAPPING` | the document root is not a mapping |
| `SCHEMA_VERSION_MISSING` | no `version` key |
| `SCHEMA_VERSION_UNSUPPORTED` | a version this engine does not implement |
| `MISSING_REQUIRED_KEY` | `claims` absent, or wrong type |
| `UNKNOWN_TOP_LEVEL_KEY` | a key the engine does not recognise |
| `STATE_NOT_RECORDED` | claims exist but no `state` block |
| `DUPLICATE_CLAIM_ID` | two claims share an id |

`UNKNOWN_TOP_LEVEL_KEY` being an error rather than a warning is deliberate. A
typo — `oracle:` for `oracles:`, say — would otherwise be ignored, and the
document would pass with its oracle definitions never read. Silence in a
verification system is not neutral, it is permissive.

## Fail-closed behaviour

| Situation | Behaviour |
| --- | --- |
| PyYAML is not importable | `DEPENDENCY_MISSING_PYYAML`, exit 3, no verdict |
| Unexpected internal error | `ENGINE_INTERNAL_ERROR`, gate FAIL, exit 1 |
| Executable oracle without permission | blocks the critical claim |
| A dimension was never exercised | reported `NOT_EXERCISED`, not PASS |
| Evidence records no observed state | unbound; cannot carry a critical claim |

An internal error produces `GATE=FAIL` rather than a crash and rather than
silence, so a broken engine can never be mistaken for a passing verification.

## Exit codes

| Code | Meaning |
| --- | --- |
| 0 | `GATE=PASS` |
| 1 | `GATE=FAIL`, including any blocking finding |
| 2 | usage or configuration error |
| 3 | missing dependency or prerequisite |

Adapters should read the verdict from the engine's own `CDV_GATE=` output line
rather than inferring it from the exit code, so that the verdict's origin is
unambiguous.

## Severity policy

| Severity | Meaning | Blocks PASS |
| --- | --- | --- |
| `ERROR` | the claim is not supported | yes |
| `WARNING` | a documented compromise, or an explanation of a FAIL | no |
| `INFO` | audit trail |

Warnings exist so that real-world trade-offs — a waived independence requirement,
a downgraded criticality, a stale-but-accepted reuse — can be *recorded* instead
of forcing a choice between hiding them and failing outright. A warning that is
never readable is a warning that is never read, so the human report shows errors
by default and warnings under `--verbose`, while the JSON and pipe-delimited
renderings always include everything.

## The deterministic summary

`cdv summary` emits stable `key=value` lines:

```
CDV_GATE=PASS|FAIL
CDV_SCHEMA_STATUS=OK|INVALID
CDV_INDEPENDENCE_GATE=...
CDV_ORACLE_QUALIFICATION_GATE=...
CDV_CONFLICT_GATE=...
CDV_FRESHNESS_GATE=...
CDV_RESIDUAL_UNCERTAINTY_GATE=...
CDV_CLAIMS / CDV_CLAIMS_PASS / CDV_CLAIMS_FAIL
CDV_CRITICAL_CLAIMS / CDV_DOWNGRADED_CLAIMS / CDV_EVIDENCE
CDV_ERRORS / CDV_WARNINGS / CDV_RESIDUAL_UNCERTAINTY
```

Each named dimension is `FAIL` if any `ERROR` in its rule family is present,
`PASS` if the document has claims and none are, and `NOT_EXERCISED` if the
document has no claims at all.

The same verdict is available as JSON (`--json`) and as one line per finding
(`cdv findings`, pipe-delimited). Every rendering is a direct function of the
same findings; none of them invents information, and none of them is more
authoritative than another.
