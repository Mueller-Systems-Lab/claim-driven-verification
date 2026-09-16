/**
 * Claim-Driven Verification -- OpenCode verification guard.
 *
 * This file is runtime-specific. All verification *rules* live in the
 * runtime-independent engine (`<project>/.verification/bin/cdv`), which this
 * adapter shells out to. The adapter never re-implements a rule and never
 * decides a verdict itself; it loads state, surfaces it to the agent, and
 * blocks the specific operations that would let an unsupported completion
 * declaration become an external fact.
 *
 * ENFORCEMENT BOUNDARY (measured on OpenCode 1.18.30, not assumed)
 * ---------------------------------------------------------------------
 * Verified available:
 *   tool.execute.before        throwing here aborts the tool call. A side effect
 *                              the call would have produced does not occur
 *                              (confirmed by world-state readback, not by the
 *                              error message alone).
 *   experimental.text.complete assistant text can be rewritten before delivery.
 *   experimental.chat.system.transform  system prompt can be extended.
 *   tool                       custom tools can be registered.
 *
 * Therefore:
 *   HARD   -- completion-adjacent tool calls (commit/push/tag/publish/merge/
 *             release) are blocked while the gate is not PASS.
 *   HARD   -- writes to the verification state or to this guard are blocked,
 *             because self-modification of the verification state is the
 *             cheapest possible way to fake a pass.
 *   ADVISORY -- assistant prose that declares completion while the gate is not
 *             PASS is stamped, not deleted. Rewriting a developer's message to
 *             say something they did not say would be its own kind of lying;
 *             marking it is honest. The stamp is not enforcement and is not
 *             presented as such.
 *
 * What this adapter cannot do: prevent a human from editing verification.yaml,
 * running git directly outside the agent, or disabling the plugin. Those are
 * outside any in-process hook's reach. The gate is a development-time control,
 * not a security boundary, and this comment is the documentation of exactly
 * where the line falls.
 */

import { tool } from "@opencode-ai/plugin"
import { execFileSync } from "node:child_process"
import { createHash } from "node:crypto"
import { appendFileSync, existsSync, readFileSync, statSync } from "node:fs"
import { join, relative, resolve } from "node:path"

// ---------------------------------------------------------------------------
// Configuration
// ---------------------------------------------------------------------------

type GuardConfig = {
  /** Extra regular expressions (as source strings) treated as completion actions. */
  completion_commands?: string[]
  /** Disable the hard blocks. Documented escape hatch; recorded in the audit log. */
  guard_disabled?: boolean
  /** Block agent writes to the verification state and to this guard. */
  selfmod_guard?: boolean
}

const DEFAULT_COMPLETION_PATTERNS: string[] = [
  // Committing or publishing is the moment a claim stops being a draft and
  // starts being a statement to the world, so it is gated.
  String.raw`\bgit\s+commit\b`,
  String.raw`\bgit\s+push\b`,
  String.raw`\bgit\s+tag\b`,
  String.raw`\bgit\s+merge\b`,
  String.raw`\bgh\s+pr\s+(create|merge|ready)\b`,
  String.raw`\bgh\s+release\s+create\b`,
  String.raw`\bnpm\s+publish\b`,
  String.raw`\bpnpm\s+publish\b`,
  String.raw`\byarn\s+publish\b`,
  String.raw`\bcargo\s+publish\b`,
  String.raw`\btwine\s+upload\b`,
  String.raw`\bdocker\s+push\b`,
  String.raw`\bkubectl\s+apply\b`,
  String.raw`\bterraform\s+apply\b`,
  String.raw`\bhelm\s+(install|upgrade)\b`,
  String.raw`\bfly\s+deploy\b`,
  String.raw`\bvercel\s+--prod\b`,
  String.raw`\bnetlify\s+deploy\b`,
]

const ENGINE_DIR_NAME = ".verification"
const GUARD_BASENAME = "verification-guard"

// ---------------------------------------------------------------------------
// Small helpers
// ---------------------------------------------------------------------------

type Gate = "PASS" | "FAIL" | "UNBOOTSTRAPPED" | "ERROR"

type EngineState = {
  gate: Gate
  bootstrapped: boolean
  engineDir: string | null
  summary: Record<string, string>
  findings: string[]
  /** Non-fatal problems with the guard itself (engine missing, spawn failure). */
  problem: string | null
  checkedAt: number
  /** Content hash of verification.yaml at the time this state was computed. */
  documentHash: string
}

function pythonExecutable(): string {
  return process.env.CDV_PYTHON || "python3"
}

function isTruthy(value: string | undefined): boolean {
  if (!value) return false
  return ["1", "true", "yes", "on"].includes(value.trim().toLowerCase())
}

function readConfig(engineDir: string): GuardConfig {
  const path = join(engineDir, "guard.config.json")
  if (!existsSync(path)) return {}
  try {
    return JSON.parse(readFileSync(path, "utf-8")) as GuardConfig
  } catch {
    // A malformed guard config must not silently disable the guard.
    return {}
  }
}

function enginePath(worktree: string): string {
  return join(worktree, ENGINE_DIR_NAME, "bin", "cdv")
}

function documentPath(worktree: string): string {
  return join(worktree, "verification.yaml")
}

let auditFailureReported = false

/**
 * Append a line to the guard's audit log.
 *
 * A failure to write the audit log is reported once to stderr, not swallowed.
 * An audit trail that silently records nothing is worse than no audit trail: it
 * makes the guard look like it ran when it did not, and this whole architecture
 * exists because "no error was reported" is not evidence that something worked.
 */
function audit(engineDir: string | null, event: string, detail: Record<string, unknown>): void {
  if (!engineDir) {
    reportAuditFailure("no engine directory resolved", event)
    return
  }
  try {
    const line = JSON.stringify({ at: new Date().toISOString(), event, ...detail })
    appendFileSync(join(engineDir, "audit.log"), line + "\n")
  } catch (error) {
    reportAuditFailure(
      `${engineDir}: ${error instanceof Error ? error.message : String(error)}`,
      event,
    )
  }
}

function reportAuditFailure(reason: string, event: string): void {
  if (auditFailureReported) return
  auditFailureReported = true
  console.error(
    `[verification-guard] audit log write failed (${reason}) while recording '${event}'. ` +
      "The guard's behaviour is unaffected, but its audit trail is incomplete.",
  )
}

// ---------------------------------------------------------------------------
// Engine invocation
// ---------------------------------------------------------------------------

const stateCache = new Map<string, EngineState>()

type DocumentFingerprint = { hash: string; mtimeMs: number; size: number }

/**
 * Cache key for a validation result.
 *
 * Keyed on the document's *content hash*, not on mtime. An mtime-plus-size key
 * can collide when a document is rewritten within the filesystem's timestamp
 * resolution and happens to keep the same length, which would serve a stale
 * verdict for the previous document -- precisely the "stale evidence" failure
 * this engine exists to reject, reintroduced in the cache.
 */
function documentFingerprint(worktree: string): DocumentFingerprint {
  const path = documentPath(worktree)
  try {
    const stat = statSync(path)
    const body = readFileSync(path)
    const hash = createHash("sha256").update(body).digest("hex")
    return { hash, mtimeMs: stat.mtimeMs, size: stat.size }
  } catch {
    return { hash: "", mtimeMs: 0, size: 0 }
  }
}

/**
 * Run the engine and translate its output into guard state.
 *
 * The verdict is always taken from the engine's own `CDV_GATE=` line. The guard
 * does not infer a verdict from exit codes, finding counts, or the presence of
 * output, because any of those could be produced by something other than the
 * engine.
 */
function queryEngine(worktree: string, force = false): EngineState {
  const engineDirCandidate = join(worktree, ENGINE_DIR_NAME)
  const engine = enginePath(worktree)

  if (!existsSync(engine)) {
    const state: EngineState = {
      gate: "UNBOOTSTRAPPED",
      bootstrapped: false,
      engineDir: null,
      summary: {},
      findings: [
        `No verification engine at ${relative(worktree, engine) || engine}.`,
        "This project has not been bootstrapped with the Claim-Driven Verification guard.",
      ],
      problem: "engine-missing",
      checkedAt: Date.now(),
      documentHash: "",
    }
    stateCache.set(worktree, state)
    return state
  }

  const cached = stateCache.get(worktree)
  const fingerprint = documentFingerprint(worktree)
  if (
    !force &&
    cached &&
    cached.bootstrapped &&
    cached.documentHash === fingerprint.hash
  ) {
    return cached
  }

  let stdout = ""
  let stderr = ""
  let ok = false
  try {
    stdout = execFileSync(pythonExecutable(), [engine, "validate", "--keyvalues"], {
      cwd: worktree,
      encoding: "utf-8",
      timeout: 120_000,
      maxBuffer: 16 * 1024 * 1024,
      env: { ...process.env, CDV_GUARD_CONTEXT: "opencode" },
    })
    ok = true
  } catch (error) {
    const err = error as { stdout?: string; stderr?: string; message?: string }
    stdout = err.stdout ?? ""
    stderr = err.stderr ?? err.message ?? ""
    // A non-zero exit is expected when the gate fails; that is a verdict, not a
    // crash. The verdict is read from stdout below.
    ok = typeof stdout === "string" && stdout.includes("CDV_GATE=")
  }

  const summary: Record<string, string> = {}
  for (const line of stdout.split("\n")) {
    const trimmed = line.trim()
    if (!trimmed.includes("=")) continue
    const index = trimmed.indexOf("=")
    const key = trimmed.slice(0, index)
    const value = trimmed.slice(index + 1)
    if (key.startsWith("CDV_")) summary[key] = value
  }

  // Collect the blocking findings, so the agent is told *what* is missing
  // rather than only that something is.
  const findings: string[] = []
  if (summary.CDV_GATE !== "PASS") {
    try {
      const listing = execFileSync(
        pythonExecutable(),
        [engine, "findings"],
        { cwd: worktree, encoding: "utf-8", timeout: 120_000, maxBuffer: 16 * 1024 * 1024 },
      )
      for (const line of listing.split("\n")) {
        const parts = line.split("|")
        if (parts.length < 5) continue
        const [severity, rule, claim, subject, message] = parts
        if (severity !== "ERROR") continue
        if (rule === "GATE_FAIL") continue
        findings.push(`${rule} [${claim}${subject !== "-" ? "/" + subject : ""}] ${message}`)
      }
    } catch {
      // Findings listing is a convenience; the verdict does not depend on it.
    }
  }

  const gate = (summary.CDV_GATE as Gate) || "ERROR"

  const state: EngineState = {
    gate,
    bootstrapped: true,
    engineDir: engineDirCandidate,
    summary,
    findings,
    problem:
      gate === "ERROR"
        ? `The engine at ${engine} did not report a verdict. stderr: ${stderr.slice(0, 500)}`
        : null,
    checkedAt: Date.now(),
    documentHash: fingerprint.hash,
  }
  stateCache.set(worktree, state)
  if (!ok && gate === "ERROR") {
    audit(engineDirCandidate, "engine-error", { stderr: stderr.slice(0, 2000) })
  }
  return state
}

// ---------------------------------------------------------------------------
// Decision helpers
// ---------------------------------------------------------------------------

function completionPatterns(engineDir: string | null): RegExp[] {
  const configured = engineDir ? readConfig(engineDir).completion_commands ?? [] : []
  const sources = [...DEFAULT_COMPLETION_PATTERNS, ...configured]
  const patterns: RegExp[] = []
  for (const source of sources) {
    try {
      patterns.push(new RegExp(source))
    } catch {
      // A malformed configured pattern is ignored rather than allowed to
      // disable the remaining patterns.
    }
  }
  return patterns
}

/**
 * Detect a bash command that would turn a claim into an external fact.
 *
 * Scans the command text, not a parsed argv, and includes quoted heredocs and
 * chained commands. This is intentionally over-inclusive: a false positive
 * costs the agent one explanation, a false negative ships an unverified claim.
 */
function completionAction(command: string, engineDir: string | null): string | null {
  const text = command.replace(/\s+/g, " ")
  for (const pattern of completionPatterns(engineDir)) {
    const match = text.match(pattern)
    if (match) return match[0]
  }
  return null
}

/**
 * Tools that only observe. Reading the verification state is not merely allowed,
 * it is encouraged: an agent that cannot inspect why the gate failed will work
 * around it instead of satisfying it. Tool names verified against OpenCode
 * 1.18.30; anything not listed here and not named `bash` falls through to the
 * path check, so a tool added by a future OpenCode release is treated as
 * write-capable rather than silently trusted.
 */
const OBSERVE_ONLY_TOOLS = new Set([
  "read",
  "grep",
  "glob",
  "list",
  "ls",
  "webfetch",
  "websearch",
  "todowrite",
  "todoread",
  "task",
  "bashoutput",
  "skill",
])

function touchedPath(args: unknown): string | null {
  if (!args || typeof args !== "object") return null
  const record = args as Record<string, unknown>
  for (const key of ["filePath", "file_path", "path", "filename", "target"]) {
    const value = record[key]
    if (typeof value === "string" && value.trim()) return value
  }
  return null
}

/**
 * Self-modification of the verification state or of this guard.
 *
 * This is the attack the architecture most needs to close: an agent that cannot
 * satisfy a claim can instead edit verification.yaml so that the claim appears
 * satisfied. Updating either artefact is a legitimate operation, but it is an
 * *installer/upgrade* operation, so it requires an explicit opt-in
 * (CDV_GUARD_ALLOW_SELFMOD=1) instead of being reachable as an ordinary step.
 *
 * Returns the offending absolute path, or null.
 */
function selfModification(
  toolName: string,
  args: unknown,
  worktree: string,
): string | null {
  const engineDir = join(worktree, ENGINE_DIR_NAME)
  const guardFile = join(worktree, ".opencode", "plugin", `${GUARD_BASENAME}.ts`)

  const isProtected = (absolute: string): boolean =>
    absolute === engineDir ||
    absolute.startsWith(engineDir + "/") ||
    absolute === guardFile

  if (toolName === "bash") {
    if (!args || typeof args !== "object") return null
    const command = (args as Record<string, unknown>).command
    if (typeof command !== "string") return null
    // Best-effort for shell redirection and in-place editors. The precise check
    // is on the file-editing tools below; this closes the obvious shell routes.
    //
    // The match is on `.verification` followed by a separator OR a word
    // boundary, so `cd .verification && echo x > VERSION` is caught as well as
    // `.verification/VERSION`. Matching on `.verification/` alone missed the
    // `cd` form, which a live canary demonstrated by rewriting the engine's
    // VERSION file. A textual heuristic cannot be airtight, and the README says
    // so; the point is not to make escape impossible but to make the ordinary
    // route blocked and any escape deliberate.
    const mentionsEngine = /\.verification(\/|\b)/.test(command)
    const mentionsGuard = command.includes(GUARD_BASENAME)
    const writes = /(>>|>|\bsed\s+-i\b|\btee\b|\bcp\b|\bmv\b|\brm\b|\btruncate\b|\bchmod\b|\binstall\b)/.test(
      command,
    )
    if ((mentionsEngine || mentionsGuard) && writes) {
      return engineDir
    }
    return null
  }

  // Observation is always permitted.
  if (OBSERVE_ONLY_TOOLS.has(toolName)) return null

  const path = touchedPath(args)
  if (!path) return null
  const absolute = resolve(worktree, path)
  return isProtected(absolute) ? absolute : null
}

// ---------------------------------------------------------------------------
// State rendering
// ---------------------------------------------------------------------------

function renderStateBlock(worktree: string, state: EngineState): string {
  const lines: string[] = []
  lines.push("<claim_driven_verification>")
  lines.push(`gate: ${state.gate}`)

  if (!state.bootstrapped) {
    lines.push(
      "status: this project has no verification engine at .verification/bin/cdv.",
    )
    lines.push(
      "mandate: completion-adjacent operations (git commit/push/tag, gh release, " +
        "publish, deploy) are BLOCKED until the project is bootstrapped. Normal " +
        "editing and testing are unaffected. Install the guard before declaring work done.",
    )
    lines.push("</claim_driven_verification>")
    return lines.join("\n")
  }

  const keys = [
    "CDV_CLAIMS",
    "CDV_CLAIMS_PASS",
    "CDV_CLAIMS_FAIL",
    "CDV_CRITICAL_CLAIMS",
    "CDV_DOWNGRADED_CLAIMS",
    "CDV_ERRORS",
    "CDV_WARNINGS",
    "CDV_SCHEMA_STATUS",
    "CDV_INDEPENDENCE_GATE",
    "CDV_ORACLE_QUALIFICATION_GATE",
    "CDV_CONFLICT_GATE",
    "CDV_FRESHNESS_GATE",
    "CDV_RESIDUAL_UNCERTAINTY_GATE",
  ]
  for (const key of keys) {
    if (state.summary[key] !== undefined) {
      lines.push(`${key.toLowerCase().replace(/^cdv_/, "")}: ${state.summary[key]}`)
    }
  }

  if (state.problem) {
    lines.push(`guard_problem: ${state.problem}`)
  }

  if (state.findings.length) {
    lines.push("blocking_findings:")
    for (const finding of state.findings.slice(0, 25)) {
      lines.push(`  - ${finding}`)
    }
    if (state.findings.length > 25) {
      lines.push(`  - ...and ${state.findings.length - 25} more; run \`.verification/bin/cdv validate\``)
    }
  }

  lines.push("")
  lines.push("mandate:")
  if (state.gate === "PASS") {
    lines.push(
      "  The gate is PASS: every declared claim is currently supported by its " +
        "required evidence. Do not describe additional work as complete unless you " +
        "have added a claim and evidence that satisfy the contract.",
    )
  } else {
    lines.push(
      "  The gate is NOT PASS. Completion-adjacent operations are blocked, and any " +
        "statement that the work is complete/verified/done would be unsupported.",
    )
    lines.push(
      "  A green test suite is not completion. To make a claim pass you must: " +
        "state the claim, enumerate its failure modes BEFORE designing evidence, " +
        "give each critical failure mode an oracle, record evidence with its " +
        "provenance dimensions, ensure at least two materially independent " +
        "evidence paths, keep evidence current for the evaluated state, and " +
        "record residual uncertainty.",
    )
    lines.push(
      "  Do not edit verification.yaml to make a claim pass. Evidence must come " +
        "from observing the system. Writes to .verification/ are blocked.",
    )
  }
  lines.push("</claim_driven_verification>")
  return lines.join("\n")
}

// ---------------------------------------------------------------------------
// Completion-declaration detection in assistant prose
// ---------------------------------------------------------------------------

const COMPLETION_PHRASES: RegExp[] = [
  /\b(?:is|are|now)\s+(?:complete|completed|done|finished|ready)\b/i,
  /\b(?:I(?:'ve| have)\s+(?:completed|finished|implemented|added|fixed))\b/i,
  /\ball\s+(?:tests?|checks?)\s+pass(?:ing|ed)?\b/i,
  /\bfully\s+(?:implemented|working|verified|tested)\b/i,
  /\bverified\s+(?:and|&)\s+(?:complete|working|done)\b/i,
  /\b(?:task|work|implementation)\s+is\s+(?:complete|done|finished)\b/i,
  /\bready\s+to\s+(?:ship|merge|release|deploy)\b/i,
  /✅/,
]

function declaresCompletion(text: string): string | null {
  // Ignore text that already carries the guard stamp, so repeated completions
  // inside one message are not re-stamped.
  const body = text.replace(/<claim_driven_verification_stamp>[\s\S]*?<\/claim_driven_verification_stamp>/g, "")
  for (const pattern of COMPLETION_PHRASES) {
    const match = body.match(pattern)
    if (match) return match[0]
  }
  return null
}

function stampFor(state: EngineState, matched: string): string {
  const blocking = state.findings.slice(0, 8)
  const lines = [
    "<claim_driven_verification_stamp>",
    "  ADVISORY -- this message contains a completion declaration that the verification gate does not support.",
    `  detected phrase : ${matched}`,
    `  gate            : ${state.gate}`,
  ]
  if (!state.bootstrapped) {
    lines.push("  reason          : this project has no verification engine installed.")
  } else if (state.problem) {
    lines.push(`  reason          : ${state.problem}`)
  } else {
    lines.push(
      `  claims          : ${state.summary.CDV_CLAIMS_PASS ?? "?"} passing, ` +
        `${state.summary.CDV_CLAIMS_FAIL ?? "?"} failing, ` +
        `${state.summary.CDV_ERRORS ?? "?"} blocking finding(s)`,
    )
  }
  if (blocking.length) {
    lines.push("  blocking findings (the claim is not sufficiently supported):")
    for (const finding of blocking) lines.push(`    - ${finding}`)
  }
  lines.push("  This stamp is advisory. The hard gate is on tool calls: commit, push, tag, publish, deploy and merge are refused while the gate is not PASS.")
  lines.push("  Resolve by adding claims and evidence to verification.yaml, then re-run: .verification/bin/cdv validate")
  lines.push("</claim_driven_verification_stamp>")
  return lines.join("\n")
}

// ---------------------------------------------------------------------------
// Plugin
// ---------------------------------------------------------------------------

export const VerificationGuard = async (input: {
  directory: string
  worktree: string
}) => {
  const worktree = input.worktree || input.directory || process.cwd()

  if (isTruthy(process.env.CDV_GUARD_DEBUG)) {
    // Diagnostic only; off unless CDV_GUARD_DEBUG is set. Exists because
    // "the guard is silent" and "the guard is running correctly" look identical
    // from the outside, and resolving that ambiguity is the point of this
    // project.
    const engine = enginePath(worktree)
    console.error(
      `[verification-guard] worktree=${worktree} directory=${input.directory} ` +
        `engine=${engine} engine_exists=${existsSync(engine)} ` +
        `document_exists=${existsSync(documentPath(worktree))} python=${pythonExecutable()}`,
    )
  }

  const currentState = (force = false): EngineState => queryEngine(worktree, force)

  const guardDisabled = (): boolean => {
    if (isTruthy(process.env.CDV_GUARD_DISABLE)) {
      audit(join(worktree, ENGINE_DIR_NAME), "guard-disabled", {
        reason: "CDV_GUARD_DISABLE set",
      })
      return true
    }
    const engineDir = join(worktree, ENGINE_DIR_NAME)
    if (existsSync(engineDir) && readConfig(engineDir).guard_disabled) {
      return true
    }
    return false
  }

  const selfmodAllowed = (): boolean => isTruthy(process.env.CDV_GUARD_ALLOW_SELFMOD)

  return {
    // -----------------------------------------------------------------------
    // Hard gate on tool calls
    // -----------------------------------------------------------------------
    "tool.execute.before": async (
      hookInput: { tool: string; sessionID: string; callID: string },
      output: { args: unknown },
    ) => {
      try {
        if (guardDisabled()) return
        const state = currentState()

        // 1. Completion-adjacent shell actions.
        if (hookInput.tool === "bash") {
          const args = (output.args ?? {}) as Record<string, unknown>
          const command = typeof args.command === "string" ? args.command : ""
          if (command) {
            const matched = completionAction(command, state.engineDir)
            if (matched && state.gate !== "PASS") {
              audit(state.engineDir, "blocked-completion-action", {
                session: hookInput.sessionID,
                call: hookInput.callID,
                gate: state.gate,
                matched,
                command: command.slice(0, 2000),
              })
              throw new Error(buildBlockMessage(state, matched, command))
            }
          }
        }

        // 2. Self-modification of the verification state or of this guard.
        if (!selfmodAllowed()) {
          const engineDir = join(worktree, ENGINE_DIR_NAME)
          const config = existsSync(engineDir) ? readConfig(engineDir) : {}
          if (config.selfmod_guard !== false) {
            const target = selfModification(hookInput.tool, output.args, worktree)
            if (target) {
              audit(join(worktree, ENGINE_DIR_NAME), "blocked-selfmodification", {
                session: hookInput.sessionID,
                call: hookInput.callID,
                tool: hookInput.tool,
                target,
              })
              throw new Error(
                [
                  "VERIFICATION_GUARD_BLOCK: modification of the verification state is not permitted from an agent session.",
                  `  tool   : ${hookInput.tool}`,
                  `  target : ${target}`,
                  "  reason : the cheapest way to fake a passing gate is to edit the " +
                    "thing the gate reads. Evidence must come from observing the system,",
                  "           not from editing the record of observation.",
                  "",
                  "  If this is a legitimate install or upgrade, re-run it with",
                  "  CDV_GUARD_ALLOW_SELFMOD=1 in the environment and it will be audited.",
                ].join("\n"),
              )
            }
          }
        }
      } catch (error) {
        // Re-throw our own deliberate blocks; swallow anything else so that a
        // guard bug cannot break the session. A swallowed guard bug is recorded.
        if (error instanceof Error && error.message.startsWith("VERIFICATION_GUARD_BLOCK")) {
          throw error
        }
        audit(join(worktree, ENGINE_DIR_NAME), "guard-internal-error", {
          where: "tool.execute.before",
          message: error instanceof Error ? error.message : String(error),
        })
      }
    },

    // -----------------------------------------------------------------------
    // Surface live state to the agent
    // -----------------------------------------------------------------------
    "experimental.chat.system.transform": async (
      _hookInput: unknown,
      output: { system: string[] },
    ) => {
      try {
        const state = currentState()
        output.system.push(renderStateBlock(worktree, state))
      } catch (error) {
        audit(join(worktree, ENGINE_DIR_NAME), "guard-internal-error", {
          where: "chat.system.transform",
          message: error instanceof Error ? error.message : String(error),
        })
      }
    },

    // -----------------------------------------------------------------------
    // Advisory stamp on unsupported completion prose
    // -----------------------------------------------------------------------
    "experimental.text.complete": async (
      _hookInput: unknown,
      output: { text: string },
    ) => {
      try {
        if (guardDisabled()) return
        if (typeof output.text !== "string" || !output.text.trim()) return
        const state = currentState()
        if (state.gate === "PASS") return
        const matched = declaresCompletion(output.text)
        if (!matched) return
        output.text = stampFor(state, matched) + "\n\n" + output.text
        audit(state.engineDir, "stamped-unsupported-completion", {
          gate: state.gate,
          matched,
        })
      } catch (error) {
        audit(join(worktree, ENGINE_DIR_NAME), "guard-internal-error", {
          where: "text.complete",
          message: error instanceof Error ? error.message : String(error),
        })
      }
    },

    // -----------------------------------------------------------------------
    // Agent-facing tools
    // -----------------------------------------------------------------------
    tool: {
      verification_status: tool({
        description:
          "Report the current Claim-Driven Verification gate state for this project: " +
          "the verdict, per-dimension gate results, blocking findings, and the residual " +
          "uncertainty recorded for each claim. Read-only. Use this before claiming work " +
          "is complete.",
        args: {},
        async execute() {
          const state = currentState(true)
          if (!state.bootstrapped) {
            return {
              title: "verification: not bootstrapped",
              output:
                "This project has no verification engine at .verification/bin/cdv, so no gate " +
                "verdict exists. Completion-adjacent operations are blocked. Install the " +
                "Claim-Driven Verification guard before declaring work complete.",
            }
          }
          const lines: string[] = []
          lines.push(`gate: ${state.gate}`)
          for (const [key, value] of Object.entries(state.summary)) {
            if (key === "CDV_GATE") continue
            lines.push(`${key}=${value}`)
          }
          if (state.problem) lines.push(`problem: ${state.problem}`)
          if (state.findings.length) {
            lines.push("")
            lines.push("blocking findings:")
            for (const finding of state.findings) lines.push(`  - ${finding}`)
          }
          return { title: `verification: ${state.gate}`, output: lines.join("\n") }
        },
      }),

      verification_gate: tool({
        description:
          "Declare that the work is complete and ask the verification gate whether that " +
          "declaration is supported. Returns PASS only when every claim in " +
          "verification.yaml is backed by current, materially independent evidence with " +
          "qualified oracles and no unresolved conflicts. Returns the blocking reasons " +
          "otherwise. This is the correct way to declare completion -- do not assert " +
          "completion in prose without it.",
        args: {
          summary_of_change: tool.schema
            .string()
            .describe("One or two sentences on what was changed, for the audit log"),
        },
        async execute(args: { summary_of_change?: string }) {
          const state = currentState(true)
          audit(state.engineDir, "completion-declaration", {
            gate: state.gate,
            summary: args.summary_of_change ?? "",
          })
          if (!state.bootstrapped) {
            return {
              title: "verification: not bootstrapped",
              output:
                "VERIFICATION_GATE=UNBOOTSTRAPPED\nNo verification engine is installed for " +
                "this project, so completion cannot be verified. Install the guard first.",
            }
          }
          const lines: string[] = [`VERIFICATION_GATE=${state.gate}`]
          if (state.gate !== "PASS") {
            lines.push("")
            lines.push("The declaration is NOT supported. Blocking reasons:")
            for (const finding of state.findings) lines.push(`  - ${finding}`)
            lines.push("")
            lines.push(
              "Do not describe the work as complete. Add claims and evidence to " +
                "verification.yaml, or fix the underlying work, then call this tool again.",
            )
          } else {
            lines.push("")
            lines.push("Every declared claim is supported by its required evidence.")
          }
          return { title: `verification_gate: ${state.gate}`, output: lines.join("\n") }
        },
      }),

      verification_refresh_state: tool({
        description:
          "Recompute the recorded evaluated state (commit and tree hash) for this project " +
          "and print the snippet to paste into verification.yaml's `state:` block. Use " +
          "after making changes, so that evidence is bound to the state it actually " +
          "describes. Requires CDV_GUARD_ALLOW_SELFMOD=1 to write; by default it only " +
          "prints.",
        args: {
          write_file: tool.schema
            .boolean()
            .optional()
            .describe("If true, rewrite the state block in verification.yaml in place"),
        },
        async execute(args: { write_file?: boolean }) {
          const engine = enginePath(worktree)
          if (!existsSync(engine)) {
            return { title: "verification: not bootstrapped", output: "No engine installed." }
          }
          try {
            const out = execFileSync(pythonExecutable(), [engine, "fingerprint"], {
              cwd: worktree,
              encoding: "utf-8",
              timeout: 120_000,
            })
            if (args.write_file && !selfmodAllowed()) {
              return {
                title: "verification: state not written",
                output:
                  out +
                  "\n\nNOT WRITTEN: writing the state block requires CDV_GUARD_ALLOW_SELFMOD=1. " +
                  "Paste the snippet above into verification.yaml yourself, or re-run the " +
                  "installer.",
              }
            }
            return { title: "verification fingerprint", output: out }
          } catch (error) {
            return {
              title: "verification: fingerprint failed",
              output: `Could not run fingerprint: ${
                error instanceof Error ? error.message : String(error)
              }`,
            }
          }
        },
      }),
    },

    // -----------------------------------------------------------------------
    // Audit trail
    // -----------------------------------------------------------------------
    event: async ({ event }: { event: { type?: string } }) => {
      try {
        const type = event?.type
        if (type === "session.idle" || type === "session.created") {
          const state = currentState()
          audit(state.engineDir, `session:${type}`, { gate: state.gate })
        }
      } catch {
        // never break the session
      }
    },
  }
}

// ---------------------------------------------------------------------------
// Block message
// ---------------------------------------------------------------------------

function buildBlockMessage(state: EngineState, matched: string, command: string): string {
  const lines: string[] = []
  lines.push(
    `VERIFICATION_GUARD_BLOCK: refusing a completion-adjacent action because the verification gate is ${state.gate}.`,
  )
  lines.push("")
  lines.push(`  matched action : ${matched}`)
  lines.push(`  command        : ${command.replace(/\s+/g, " ").slice(0, 300)}`)
  lines.push("")

  if (!state.bootstrapped) {
    lines.push("  This project has no verification engine at .verification/bin/cdv, so no")
    lines.push("  completion claim can be verified. Install the Claim-Driven Verification")
    lines.push("  guard, then retry.")
  } else {
    if (state.problem) {
      lines.push(`  guard problem  : ${state.problem}`)
    }
    lines.push(
      `  claims         : ${state.summary.CDV_CLAIMS_PASS ?? "?"} passing, ` +
        `${state.summary.CDV_CLAIMS_FAIL ?? "?"} failing`,
    )
    if (state.findings.length) {
      lines.push("")
      lines.push("  Blocking findings:")
      for (const finding of state.findings.slice(0, 12)) {
        lines.push(`    - ${finding}`)
      }
    }
    lines.push("")
    lines.push("  A green test suite is not completion. A completion claim becomes PASS")
    lines.push("  when its critical failure modes each have a qualified oracle and")
    lines.push("  materially independent, current evidence, and its residual uncertainty is")
    lines.push("  recorded.")
    lines.push("")
    lines.push("  Inspect:  .verification/bin/cdv validate")
    lines.push("  Machines: .verification/bin/cdv summary")
  }

  lines.push("")
  lines.push(
    "  This block is a development-time control. To bypass it deliberately, set " +
      "CDV_GUARD_DISABLE=1; the bypass is written to .verification/audit.log.",
  )
  return lines.join("\n")
}
