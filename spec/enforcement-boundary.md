# The enforcement boundary

This document states exactly where enforcement is hard, where it is advisory, and
where it does not reach at all. It exists because a verification system whose
enforcement boundaries are vague is worse than one with weaker but *known*
enforcement: vague boundaries get trusted further than they should.

Everything below was **measured** on the installed runtime, not inferred from
documentation. Where a behaviour was assumed and then found to be different, the
correction is recorded, because a measurement that had to be corrected is more
useful to a reader than a claim that was always right.

## The measured runtime

| Item | Value at the time of writing |
| --- | --- |
| Runtime | OpenCode `1.18.30` (compiled Bun binary) |
| Plugin API package | `@opencode-ai/plugin` `1.18.20` |
| Engine | CPython 3.12.3, PyYAML 6.0.1 |
| Plugin discovery glob | `{plugin,plugins}/*.{ts,js}` |
| Verification document | `verification.yaml`, schema v1 |

### Plugin discovery — measured

The discovery glob in the runtime is:

```js
glob("{plugin,plugins}/*.{ts,js}", { cwd: <config-or-project dir>, include: "file", dot: true })
```

Two consequences that shaped the installer, both of which would have been got
wrong by assuming the documented layout:

1. **Files must be flat.** `verification-guard.ts` must sit directly in
   `.opencode/plugin/` (or `.opencode/plugins/`). A guard nested at
   `.opencode/plugin/verification-guard/index.ts` is never loaded, silently. The
   installer writes it flat and the independent readback asserts the position,
   because a silently-unloaded guard looks exactly like a passing project.
2. **Both `plugin` and `plugins` work.** The installer prefers an existing
   directory and only creates `plugin/` when neither exists.

## Enforcement mechanisms — each measured, not assumed

### 1. `tool.execute.before` — HARD

Throwing an `Error` from this hook aborts the tool call. This was verified with a
**world-state readback**, not by reading the error message: a plugin was
configured to block a command that creates a marker file, and the file was then
checked for on disk.

| Action | Result |
| --- | --- |
| `touch .../BLOCKED_MARKER` (matching the block pattern) | file **absent** |
| `touch .../CONTROL_MARKER` (not matching) | file **present** |

The command genuinely did not execute. An error message alone would not have
shown that, which is the same reason this project refuses to accept an
application's own success message as evidence.

This is the mechanism the completion gate is built on. What it blocks:

- **Completion-adjacent shell actions**: `git commit`, `git push`, `git tag`,
  `git merge`, `gh pr create|merge|ready`, `gh release create`, `npm|pnpm|yarn|
  cargo publish`, `twine upload`, `docker push`, `kubectl apply`,
  `terraform apply`, `helm install|upgrade`, `fly deploy`, `vercel --prod`,
  `netlify deploy`. Projects can add their own through
  `.verification/guard.config.json`.
- **Writes to `.verification/`** and to the guard file itself. The cheapest way
  to fake a passing gate is to edit the thing the gate reads, so this is closed
  by default and requires `CDV_GUARD_ALLOW_SELFMOD=1` to reopen.
- Reading `.verification/` is always permitted. An agent that cannot inspect why
  the gate failed will work around it instead of satisfying it.

### 2. `experimental.text.complete` — ADVISORY

Assistant text can be rewritten before delivery (measured: a literal marker token
in the model's output was replaced). The guard uses this to **prepend a stamp**
when a message declares completion while the gate is not PASS.

It is described as advisory because it is. The stamp marks the claim; it does not
retract it. Rewriting a developer's message to say something they did not say
would be its own kind of lying, and the point of this system is to make the
record trustworthy. **The hard gate is on tool calls.** The stamp is the
thing that makes an unsupported completion declaration legible to a reader who
only sees the transcript.

### 3. `experimental.chat.system.transform` — INFORMATIONAL

The system prompt can be extended (the hook accepts appends to `output.system`).
The guard injects the live gate state: verdict, claim counts, per-dimension
results, and the blocking findings. This is how the agent learns why it is
blocked without having to guess, and it is what makes the injected mandate
concrete rather than a general exhortation to be careful.

### 4. `tool` — AGENT INTERFACE

Custom tools can be registered from a plugin (measured: a probe tool was
registered and called). The guard exposes:

| Tool | Purpose |
| --- | --- |
| `verification_status` | gate verdict, dimensions, blocking findings |
| `verification_gate` | declare completion; returns the gate's answer and, on failure, the blocking reasons |
| `verification_refresh_state` | recompute the recorded evaluated state |

`verification_gate` is the supported way to declare completion. Its value is that
it converts "I think I'm done" into a recorded, answered question.

### 5. `cdv context-proof` — CONTEXT INTEGRITY (v1.0.1)

Not a runtime hook: a check the verifier must pass *before* interpreting any
behavioural result. It establishes that the intended target is being observed,
using indicators that cannot be satisfied by inheriting stale state, and fails
closed on any contradiction. See `regression-v1.0.0-false-pass.md` for the
false PASS that made it necessary and `failure-mode-model.md` for the failure
mode it addresses.

The reason it belongs in the enforcement boundary rather than in the test
suite is that it constrains what the guard's evidence is allowed to *mean*.
A block observed in the wrong directory is not a block.

### 6. `event` — AUDIT

Runtime events are delivered (measured). The guard appends to
`.verification/audit.log`, and the enforcement canary reads that log as an
independent observation channel: it is written by the runtime process and read by
a separate one, so it shows whether the hooks actually ran rather than merely
whether the plugin was installed.

An audit write failure is **reported once to stderr, not swallowed**. An audit
trail that silently records nothing makes the guard look like it ran when it did
not, which is the exact class of false assurance this project exists to remove.

## What is not enforced, and cannot be

| Situation | Why it is out of reach |
| --- | --- |
| A human edits `verification.yaml` directly | Outside any in-process hook. The gate is a development-time control, not a filesystem permission. |
| Git run outside the agent | Same. The guard sees tool calls, not the operator's terminal. |
| The plugin being disabled | `CDV_GUARD_DISABLE=1`, `--pure`, or removing the file. Attempts through the guarded shell route are blocked; the rest is outside the process. |
| A project that never writes honest evidence | No mechanism can detect a lie that is internally consistent. The gate makes lying *more expensive and more visible*, not impossible. |
| Bash self-modification routes | The shell check is a textual heuristic. It matches `.verification` followed by a separator or word boundary plus an obvious write operator, which closes the ordinary routes including `cd .verification && ... > VERSION`. A sufficiently obfuscated command can evade it. See below. |

### The honest statement about the shell heuristic

The precise anti-tamper check is on the file-editing tools (`write`, `edit`),
where the target path is available structurally. The check on shell commands is a
**heuristic**, and it was strengthened once already: a live canary demonstrated
that matching `.verification/` alone missed `cd .verification && echo x > VERSION`,
because the directory name was followed by a space rather than a slash. That gap
is now closed.

The residual gap is real and is stated rather than implied away: a deliberately
obfuscated shell command can write to `.verification/`. The design position is
that the goal is not to make escape impossible — an in-process hook cannot — but
to make the ordinary route blocked and any escape *deliberate*, auditable, and
therefore attributable. `CDV_GUARD_ALLOW_SELFMOD=1` is the documented, audited
route for legitimate installs and upgrades.

### Tool names are measured, not assumed

| Tool | Arguments observed |
| --- | --- |
| `bash` | `command` |
| `write` | `filePath`, `content` |
| `edit` | `filePath`, `oldString`, `newString` |
| `read` | `filePath` |

Anything not in the observe-only list falls through to the path check, so a tool
added by a future runtime release is treated as write-capable rather than
silently trusted. Fail-closed on the unknown.

## A model may comply before the guard has to enforce

Found while running the end-to-end canary against a second provider, and worth
recording because it constrains what an enforcement test can demonstrate.

The guard injects the live gate state into the system prompt. A model that reads
it can conclude on its own that `.verification/` is protected infrastructure and
**decline to attempt the write at all**. Observed directly: with a failing
verification document in place, one model answered *"the `.verification/`
directory is protected by the claim-driven verification gate — writes to it are
blocked"* and never made the tool call. The same model, with a passing document
in place, made the call and was blocked.

From the outside these two runs look identical if you only check the file
afterwards:

| Run | Guard decision | File unchanged? |
| --- | --- | --- |
| agent attempted, guard refused | blocked | yes |
| agent declined to attempt | never invoked | yes |

"The file was not modified" is satisfied by both. This is the same shape as the
v1.0.0 false PASS, one level up: an assertion about absence cannot distinguish
"prevented" from "never happened".

So the canary requires the guard's own audit log to record a refusal *appended
during that step* before it treats the enforcement path as exercised, and reports
`ANTI_TAMPER=NOT_PROVOKED_STATE_UNCHANGED` — explicitly untested — when no
attempt could be provoked. `tests/verify.sh` then requires
`ANTI_TAMPER=REFUSED_AND_RECORDED` from the **primary** provider, so the coverage
is guaranteed at release level even when a particular model declines.

### How coverage is therefore guaranteed

The enforcement claim is conditional -- *if* a completion-adjacent action is
attempted at gate ≠ PASS, the guard blocks it. The antecedent needs an attempt,
and a well-behaved model will not supply one. So the pipeline separates the two
things being shown:

| Claim | How it is verified | Model-dependent? |
| --- | --- | --- |
| The mechanism: a matched completion action is refused, with no side effect | provoke a **configured** completion pattern (`touch CDV_E2E_MARKER`, registered through `guard.config.json`) | no -- the model has no reason to decline a harmless command |
| The policy: which commands count as completion actions | `static_checks.py` reads the guard's default pattern list and asserts all 14 expected commands are present | no |
| The policy, end to end | a **real** `git commit` is provoked; `tests/verify.sh` requires the primary provider to have refused one | once, at release level |
| Writes to `.verification/` are refused | provoke, three routes; `verify.sh` requires the primary provider to have recorded a refusal | once, at release level |
| No bypass occurred, on every provider | the file / commit count is asserted unchanged | no |

The per-provider assertions are the ones that cannot be faked: a *successful*
write or commit changes the world and is caught regardless of whether the attempt
was provoked. What a declining model costs is coverage of the refusal path, not
detection of a failure -- and coverage is required once, from a provider that
does attempt it.

Two things follow, and both are limits rather than defects:

- **Model cooperation is a precondition for probing the guard, not for the guard
  working.** Enforcement is deterministic; provoking it is not.
- **A model that obeys the guard's stated intent is not evidence that the guard
  enforced anything.** It is evidence that the model was cooperative. Only the
  audit trail, written by the runtime, distinguishes them.

This is also why the anti-tamper shell check is described as a heuristic rather
than a boundary, and why no attempt was made in v1.0.1 to strengthen it further:
no reproducible bypass was found, and speculative command-obfuscation detection
would add complexity without closing anything observed.

## Records that are never trusted as decisions

| Observation | Why it cannot decide a verdict |
| --- | --- |
| The plugin saying it loaded | A plugin that fails to load is silent. The canary requires audit-log evidence that hooks *ran*. |
| The installer saying it succeeded | Replaced by the independent readback, which re-hashes the installed files against the manifest and against the bootstrap source. |
| The engine's exit code alone | The verdict is read from the engine's own `CDV_GATE=` line so its origin is unambiguous. |
| An error message saying something was blocked | Replaced by world-state readback: the marker file is absent, the commit count is unchanged. |
| A guard reporting "no problems" | `cdv` reports `NOT_EXERCISED` for dimensions it was never given anything to check. |

## A measured failure of the test harness itself

(The full incident record is `regression-v1.0.0-false-pass.md`. The summary
below is retained because it belongs in the boundary discussion: it is the
clearest example of a check that could not audit its own vantage point.)

Worth recording because it is the most instructive thing found during
development, and because it is the failure mode this whole architecture is built
to prevent.

The end-to-end canary initially reported its blocking checks as passing. They
were passing **vacuously**. The canary launched the agent with
`subprocess(cwd=target)`, but `PWD` in the inherited environment still pointed at
the caller's repository, and the runtime resolves the session's project from
`PWD`. So the agent was working in a different repository entirely, while the
canary inspected the target:

- "the marker file was not created" — true, because the agent was never in that
  directory
- "the commit count did not change" — true, for the same reason
- the audit log was empty — because the guard in the target repository had never
  been exercised

Five checks reported success against a system that had not been tested at all.
It was caught only because a *different* check — "the verification state was not
modified" — failed, since that assertion used an absolute path.

Two changes came out of it, and they are the ones that matter:

1. `PWD` is set explicitly for the agent subprocess.
2. The canary now **aborts before interpreting anything** unless it can show the
   agent ran in the target project and that the guard's hooks actually fired. A
   later passing assertion cannot be believed without that evidence, so it is not
   evaluated without it.

This is the architecture's own argument applied to its own tests: an assertion
that passed is not evidence until you have shown it could have failed.
