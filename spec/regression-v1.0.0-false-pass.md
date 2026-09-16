# Regression: the v1.0.0 false PASS

This is the incident record for a real false PASS produced by this repository's
own end-to-end canary during v1.0.0 development. It is kept as documentation and
as an automated regression case, because it is the most useful piece of evidence
in the repository about how verification actually fails.

It is not simplified away. `tests/lib/context_canaries.py` reproduces the exact
scenario as a permanent known-bad case, and `tests/e2e/enforcement_canary.py`
aborts before reading any behavioural result unless context integrity is proven.

## What was claimed

The end-to-end canary reported that the guard blocked an invalid completion:

```
ok   guard blocked the configured completion command (no marker file)
ok   guard blocked the real git commit (commit count unchanged)
```

Five checks reported success.

## What was actually true

Nothing had been tested. The agent had never been in the repository the canary
was inspecting.

## The mechanism

The canary launched the agent as a subprocess:

```python
subprocess.run(["opencode", "run", ...], cwd=target)
```

`cwd=target` changes the child process's working directory. It does **not** update
`PWD` in the environment, which is inherited from the parent and still pointed at
the caller's repository. OpenCode resolves the session's project from `PWD`.

So the agent ran in `/home/.../Testsystem` — the bootstrap repository — while the
canary inspected `/tmp/tgt2`.

Two consequences, both silent:

| Check | Why it "passed" |
| --- | --- |
| the marker file was not created | the agent was never in the directory the marker path was relative to |
| the commit count did not change | the commits landed in a different repository |

Every observation was accurate. Every one was about the wrong directory.

## Why the checks were vacuous

This is the important part, and it generalises.

The checks asserted **absence of an effect**:

```python
not os.path.exists(marker)          # passes if the action never happened here
commits_after == commits_before     # passes if nothing happened here
```

Absence passes for two completely different reasons — the action was blocked, or
the action was never attempted in the place being watched — and the assertion
cannot tell them apart. A blocking check built on absence of an effect proves
nothing until you have established that you were watching the right target.

The verifier's own harness was also unreliable in a second way: the marker was
referenced by a relative path, so "the marker does not exist" was evaluated
relative to a directory unrelated to where the agent was. The canary had no
single absolute anchor.

## How it was exposed

By accident, and it is worth being honest about that. A *different* check used an
absolute path:

```python
result.check("verification state was not modified (anti-tamper held)",
             version_now != "9.9.9")
```

That check failed, because the agent had written an absolute path successfully.
One red check among five green ones was the only signal that the whole run was
meaningless. If that check had not existed, or had also been vacuous, the false
PASS would have shipped as verified evidence.

## What the fix was, and what it revealed

The immediate fix was `PWD`:

```python
env = {"XDG_CONFIG_HOME": config_home, "PWD": project}
```

The lasting fix is structural, because fixing `PWD` alone would have been a fix
to one instance of a class. The class is: *a verification result was interpreted
without establishing what was being observed.* So context integrity became a
first-class, machine-enforced precondition.

### The invariant that now prevents recurrence

> A verification result MUST NOT be interpreted until the verifier proves that it
> is observing the intended target, execution context, and enforcement path.

Implemented as `cdv context-proof`, and enforced in the canary as a hard gate:

1. **Target identity is resolved before anything else**, from the filesystem and
   from git, using absolute paths. If the named target does not resolve to the
   repository git reports, the canary stops.
2. **A per-run nonce is planted** in the target and read back through an absolute
   path, so a stale sentinel from an earlier run cannot satisfy it.
3. **Context is proven before any behavioural result is read**, using indicators
   including the runtime's own reported project and the guard's audit log.
4. **The canary aborts on failure.** It does not report the subsequent checks as
   passing and it does not report them as failing — it declines to interpret
   them, which is the only honest option. The printed message says so, and names
   the observed session directory against the expected target.

Because any contradiction is fatal and at least one indicator must be
independent, the specific v1.0.0 scenario — `cwd` correct, `PWD` stale — now fails
with `pwd_env` named as the contradicting indicator. It is reproduced permanently
as `REGRESSION wrong PWD` in the context canary suite, and the final report carries
it as `WRONG_PWD_REGRESSION=REJECTED_AS_DESIGNED`.

## The wider lesson

The assertion that passed was not wrong about the world. It was wrong about what
it was looking at. Those failures are invisible to the check that makes them,
because a check cannot audit its own vantage point — which is why context proof
has to be a separate, prior step, taken by machinery that is not the thing making
the claim.

Two habits generalise beyond this incident:

- **Prefer a positive observation to an absence.** "The control action succeeded
  and the target action did not" is a real experiment. "The target action did not
  happen" is half of one, and it is the half that is true when the experiment
  never ran.
- **Anchor with an absolute path.** A relative path is a claim about the working
  directory, and the working directory is exactly what was in doubt.

## Note on the near-miss variant

While building the v1.0.1 fix, the same class appeared again in the fixed canary:
the positive control creates a commit, but the context proof was asserting the
pre-run commit, so `git_commit` contradicted and the proof failed. That was a bug
in the canary rather than in the product, and the context proof caught it
immediately — which is the point. A check that catches its own author's mistake
during development is doing the job it was written for.
