# Installation

## Prerequisites

| Requirement | Needed for | Fail-closed behaviour if missing |
| --- | --- | --- |
| `python3` (3.9+) | the verification engine | installer exits 3 at preflight |
| PyYAML | reading `verification.yaml` | installer exits 3; the engine exits 3 rather than falling back to an approximate parser |
| OpenCode (for the guard) | runtime enforcement | the guard is installed but enforcement is unverified, so the bootstrap reports FAIL |
| git | state fingerprinting, the positive control | falls back to a content hash; the enforcement canary's positive control needs a repository |
| a model provider configured in OpenCode | the enforcement canary | pass `--e2e-model`, or the bootstrap reports FAIL |

PyYAML is the *only* external Python dependency. The engine is otherwise
standard library. It is checked rather than optional: a second, hand-rolled
parser would be a second set of failure modes for the one file whose correct
interpretation the whole system rests on.

```bash
python3 -m pip install PyYAML     # if not already present
```

## Install

```bash
./installer/install.sh /path/to/target-project
```

### Options

| Option | Effect |
| --- | --- |
| `--no-e2e` | Skip the end-to-end canary. **The bootstrap result will be FAIL**, because the guard's behaviour will not have been demonstrated. |
| `--e2e-model PROVIDER/MODEL` | Model for the enforcement canary. Defaults to `$CDV_E2E_MODEL`, then to the first `deepseek/*` model reported by `opencode models`, then to the first model overall. The choice is printed. |
| `--e2e-timeout SECONDS` | Per-agent-call timeout (default 420). |
| `--keep-temp` | Keep the temporary target project used for enforcement and print its path. |
| `--force` | Reinstall even when the installed version is newer. |
| `--allow-self-install` | Permit installing into the bootstrap repository itself. Refused by default as circular. |
| `--quiet` / `--verbose` | Less narrative / pass through the canary agent's raw output. |

### What it writes

```
<target>/.verification/
  bin/cdv                      engine launcher
  core/cdv/*.py                the engine
  core/templates/              starter document
  schema/                      the published JSON Schema
  tests/                       canary suite, edge cases, E2E, readback
  guard.config.json            guard configuration
  manifest.json                version + hash of every installed file
  .gitignore                   keeps audit.log out of your history
  VERSION
<target>/.opencode/plugin/verification-guard.ts    the runtime guard
<target>/verification.yaml                         created only if absent
```

This is a project's whole footprint: one directory, one plugin file, one document.

### Guarantees

**Idempotent.** Re-running upgrades an existing installation.

**Non-destructive.** `verification.yaml` is never overwritten. An installation
being replaced is moved to `.verification.backup.<timestamp>` first. Re-installing
repeatedly therefore leaves one backup per upgrade. They are never deleted
automatically — removing a previous installation is a destructive action and
better done deliberately than by a script. Clean them up when you are satisfied
the upgrade is good:

```bash
ls -d /path/to/target/.verification.backup.*     # review
rm -rf /path/to/target/.verification.backup.*    # remove
```

**Version-aware.** A newer installed version is not silently downgraded; the
installer refuses and tells you to pass `--force`.

**Fail-closed.** `VERIFICATION_BOOTSTRAP=PASS` requires that the installed engine
rejects known-bad input under its own canary suite, that all five gate
dimensions were driven to failure by the canary meant to exercise them, that the
context integrity canaries pass against the *installed* engine, that a real
runtime blocked an invalid completion while permitting a valid one, and that the
installation independently read back as matching its manifest. Copying files is
not on that list.

**Non-destructive to your verification state.** `verification.yaml` is never
overwritten and is never made write-protected. Recording claims, failure modes,
evidence and residual uncertainty is the workflow; the engine validates changes
rather than forbidding them. The separate property — that editing the state
cannot buy a PASS — is tested explicitly by the end-to-end canary.

### Exit codes

| Code | Meaning |
| --- | --- |
| 0 | `VERIFICATION_BOOTSTRAP=PASS` |
| 1 | `VERIFICATION_BOOTSTRAP=FAIL` |
| 2 | usage error |
| 3 | missing prerequisite |

### Final classification

`FINAL_CLASSIFICATION` is not hardcoded. Both the installer and `tests/verify.sh`
read the canonical label from the `CLASSIFICATION` file at the repository root
(`CLASSIFICATION_VERIFIED` / `CLASSIFICATION_INCOMPLETE`), which is why the two
reports cannot disagree. The label names the release it describes, and the static
checks fail if it stops matching `VERSION`. A published release additionally
reports a classification describing the publication, which is recorded in the
publication report rather than in this file.

## Verify an existing installation

```bash
<target>/.verification/tests/lib/canary_runner.py       # canaries
<target>/.verification/tests/lib/static_checks.py       # source consistency
<target>/.verification/tests/lib/context_canaries.py    # context integrity
<target>/.verification/tests/e2e/enforcement_canary.py \
    --project <target> --model PROVIDER/MODEL           # guard enforcement
python3 <target>/.verification/tests/independent_readback.py \
    --project <target> --source /path/to/bootstrap --expect-version 1.0.0
```

Or validate the bootstrap repository end to end:

```bash
./tests/verify.sh --model deepseek/deepseek-flash
```

## Uninstall

```bash
rm -rf /path/to/target/.verification
rm -f  /path/to/target/.opencode/plugin/verification-guard.ts
# verification.yaml is yours; delete it yourself if you want it gone
```

## Schema migration

The document declares its schema version, and the engine **refuses** to evaluate a
version it does not implement rather than guessing at the differences.

| From | To | Action |
| --- | --- | --- |
| 1 | 1 | none |

A future migration will ship as `installer/migrate-vN-to-vM.py`, and the engine's
refusal message names the migration to run. Until then, schema `v1` is the only
version.

## Troubleshooting

**`cdv: error [DEPENDENCY_MISSING_PYYAML]`** — install PyYAML. The engine
deliberately does not fall back to an approximate parser.

**`VERIFICATION_BOOTSTRAP=FAIL` with `GUARD_ENFORCEMENT_E2E=UNVERIFIABLE`** —
`opencode` is not on `PATH`, so the guard's behaviour could not be demonstrated.
This is the fail-closed outcome and it is accurate: enforcement is not verified.
Install OpenCode, or accept that the installation is unverified and that the
report says so.

**`INDEPENDENT_READBACK=FAIL` listing drifted files** — something modified the
installed files after installation. Run the readback to see which; re-run the
installer to restore them, or investigate, because files under `.verification/`
are not meant to change at runtime.

**`CONTEXT_PROOF=FAIL`** — the verifier could not establish that it was observing
the intended target, so it refused to interpret its results. The output names the
contradicting indicator. `pwd_env` means `PWD` disagrees with the real working
directory: run the check from inside the target, or set `PWD` to match. This is
the v1.0.0 false PASS being caught, not a spurious failure — see
`spec/regression-v1.0.0-false-pass.md`.

**The guard does not block anything** — confirm the file is at
`.opencode/plugin/verification-guard.ts` and *not* nested in a subdirectory.
OpenCode discovers `{plugin,plugins}/*.{ts,js}` and ignores nested files
silently. Then set `CDV_GUARD_DEBUG=1` to confirm the guard resolves the right
worktree.

**The gate is FAIL and I cannot commit** — that is the gate working. Run
`.verification/bin/cdv validate` to see the blocking findings; each names the rule
it violated and what is missing. `.verification/bin/cdv findings` gives one line
per finding for scripting.

**I need to make a deliberate exception** — the escape hatches are
`CDV_GUARD_ALLOW_SELFMOD=1` (permit writes to `.verification/` and the guard, for
legitimate installs and upgrades) and `CDV_GUARD_DISABLE=1` (disable the hard
blocks). Both are recorded in `.verification/audit.log`. Neither changes a gate
verdict; they only change what is blocked.
