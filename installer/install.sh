#!/usr/bin/env bash
#
# Claim-Driven Verification -- bootstrap installer.
#
#   ./installer/install.sh /path/to/target-project
#
# Installs the runtime-independent verification engine, the OpenCode verification
# guard, and this project's starter verification.yaml, then *demonstrates* that
# the installation works before reporting success.
#
# Principles
# ----------
#   * Idempotent. Re-running over an existing installation upgrades it.
#   * Non-destructive. verification.yaml is never overwritten. Any replaced
#     installation is moved to a timestamped backup first.
#   * Version-aware. A newer installed version is not silently downgraded.
#   * Fail-closed. VERIFICATION_BOOTSTRAP=PASS is printed only after the
#     installed engine rejects known-bad input AND the installed guard is shown
#     to block an invalid completion while permitting a valid one. Files being
#     copied successfully is not evidence of anything and never yields PASS.

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SRC_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

# ---------------------------------------------------------------------------
# Output helpers
# ---------------------------------------------------------------------------
if [ -t 1 ] && [ -z "${NO_COLOR:-}" ]; then
  C_GREEN=$'\033[32m'; C_RED=$'\033[31m'; C_YELLOW=$'\033[33m'
  C_BOLD=$'\033[1m'; C_DIM=$'\033[2m'; C_RESET=$'\033[0m'
else
  C_GREEN=""; C_RED=""; C_YELLOW=""; C_BOLD=""; C_DIM=""; C_RESET=""
fi

QUIET=0
ok()   { printf '  %sok%s   %s\n' "$C_GREEN" "$C_RESET" "$*"; }
warn() { printf '  %swarn%s %s\n' "$C_YELLOW" "$C_RESET" "$*"; }
err()  { printf '  %serr%s  %s\n' "$C_RED" "$C_RESET" "$*" >&2; }
section() { [ "$QUIET" -eq 1 ] || { printf '\n%s%s%s\n' "$C_BOLD" "$*" "$C_RESET"; }; }

# ---------------------------------------------------------------------------
# Defaults / arguments
# ---------------------------------------------------------------------------
TARGET=""
RUN_E2E=1
E2E_MODEL="${CDV_E2E_MODEL:-}"
KEEP_TEMP=0
FORCE=0
ALLOW_SELF_INSTALL=0
VERBOSE=0
E2E_TIMEOUT="${CDV_E2E_TIMEOUT:-420}"

usage() {
  cat <<'EOF'
Claim-Driven Verification -- bootstrap installer

Usage:
  ./installer/install.sh [options] /path/to/target-project

Options:
  --no-e2e                 Skip the end-to-end guard enforcement canary.
                           The bootstrap result will then be FAIL, because the
                           guard's behaviour will not have been demonstrated.
  --e2e-model PROVIDER/MODEL
                           Model used for the enforcement canary.
                           Default: $CDV_E2E_MODEL, else auto-detected.
  --e2e-timeout SECONDS    Per-agent-call timeout for the canary (default 420).
  --keep-temp              Keep the temporary target project used for the
                           enforcement canary, and print its path.
  --force                  Reinstall even if the installed version is newer.
  --allow-self-install     Permit installing into the bootstrap repository
                           itself (normally refused, as it is circular).
  --quiet                  Suppress the progress narrative; the final report
                           is always printed.
  --verbose                Pass through the canary agent's raw output.
  -h, --help               This message.

Exit status:
  0  VERIFICATION_BOOTSTRAP=PASS
  1  VERIFICATION_BOOTSTRAP=FAIL
  2  usage error
  3  missing prerequisite
EOF
}

while [ $# -gt 0 ]; do
  case "$1" in
    --no-e2e) RUN_E2E=0 ;;
    --e2e-model) E2E_MODEL="${2:-}"; shift ;;
    --e2e-timeout) E2E_TIMEOUT="${2:-}"; shift ;;
    --keep-temp) KEEP_TEMP=1 ;;
    --force) FORCE=1 ;;
    --allow-self-install) ALLOW_SELF_INSTALL=1 ;;
    --quiet) QUIET=1 ;;
    --verbose) VERBOSE=1 ;;
    -h|--help) usage; exit 0 ;;
    -*) err "unknown option: $1"; usage; exit 2 ;;
    *)
      if [ -n "$TARGET" ]; then err "only one target directory may be given"; exit 2; fi
      TARGET="$1" ;;
  esac
  shift
done

if [ -z "$TARGET" ]; then err "no target project directory given"; usage; exit 2; fi

TARGET="$(cd "$TARGET" 2>/dev/null && pwd || true)"
if [ -z "$TARGET" ]; then err "target directory does not exist"; exit 2; fi
if [ ! -d "$TARGET" ]; then err "target is not a directory: $TARGET"; exit 2; fi

# ---------------------------------------------------------------------------
# Result collection
# ---------------------------------------------------------------------------
declare -A R
R[ENGINE_STATUS]=PENDING
R[GUARD_STATUS]=PENDING
R[SCHEMA_STATUS]=PENDING
R[CANARY_STATUS]=PENDING
R[STATIC_STATUS]=PENDING
R[E2E_STATUS]=NOT_RUN
R[READBACK_STATUS]=NOT_RUN
R[CONTEXT_STATUS]=PENDING
R[OPENCODE_RUNTIME]=NOT_DETECTED
R[OPENCODE_VERSION]=unknown
R[FILES_INSTALLED]=0
R[EXISTING_VERSION]=none

fatal() {
  err "$1"
  R[FATAL]="$1"
  R[ENGINE_STATUS]=FAIL
}

# ---------------------------------------------------------------------------
# 1. PREFLIGHT
# ---------------------------------------------------------------------------
section "1/12  PREFLIGHT"

if [ ! -f "${SRC_ROOT}/VERSION" ]; then
  fatal "source repository is incomplete: ${SRC_ROOT}/VERSION is missing"
  exit 3
fi
BOOTSTRAP_VERSION="$(tr -d '[:space:]' < "${SRC_ROOT}/VERSION")"
ok "bootstrap version ${BOOTSTRAP_VERSION}"

PYTHON_BIN="${CDV_PYTHON:-$(command -v python3 || true)}"
if [ -z "$PYTHON_BIN" ]; then
  fatal "python3 is required but was not found on PATH"
  exit 3
fi
PY_VERSION="$("$PYTHON_BIN" --version 2>&1)"
ok "python: ${PY_VERSION} (${PYTHON_BIN})"

if ! "$PYTHON_BIN" -c "import yaml" 2>/dev/null; then
  fatal "PyYAML is required but is not importable by ${PYTHON_BIN}.
       Install it with: ${PYTHON_BIN} -m pip install PyYAML
       Refusing to install an engine that cannot read its own canonical document."
  exit 3
fi
ok "PyYAML present"

for required in core/cdv/cli.py bin/cdv templates/verification.yaml \
                schema/verification.schema.json \
                runtime/opencode/verification-guard.ts \
                tests/lib/canaries.py tests/lib/canary_runner.py \
                tests/lib/static_checks.py \
                tests/lib/context_canaries.py \
                tests/lib/edge_cases.py \
                tests/lib/shell_check.py \
                tests/lib/packaging_check.py \
                CLASSIFICATION \
                tests/e2e/enforcement_canary.py \
                tests/independent_readback.py; do
  if [ ! -e "${SRC_ROOT}/${required}" ]; then
    fatal "source repository is incomplete: missing ${required}"
    exit 3
  fi
done
ok "source repository complete"

if [ ! -w "$TARGET" ]; then
  fatal "target directory is not writable: ${TARGET}"
  exit 3
fi
ok "target writable: ${TARGET}"

if [ "$TARGET" = "$SRC_ROOT" ] && [ "$ALLOW_SELF_INSTALL" -ne 1 ]; then
  fatal "refusing to install the bootstrap repository into itself (circular).
       Pass --allow-self-install if you really mean to do this."
  exit 3
fi

DRY_MARKER="${TARGET}/.cdv-install-probe-$$"
if ! touch "$DRY_MARKER" 2>/dev/null; then
  fatal "cannot create files in ${TARGET}"
  exit 3
fi
rm -f "$DRY_MARKER"
ok "preflight passed"
R[PREFLIGHT]=PASS

# ---------------------------------------------------------------------------
# 2. DETECT THE RUNTIME
# ---------------------------------------------------------------------------
section "2/12  OPENCODE RUNTIME DETECTION"
if command -v opencode >/dev/null 2>&1; then
  R[OPENCODE_RUNTIME]=PRESENT
  R[OPENCODE_VERSION]="$(opencode --version 2>/dev/null | head -1 | tr -d '[:space:]')"
  [ -n "${R[OPENCODE_VERSION]}" ] || R[OPENCODE_VERSION]=unknown
  ok "opencode ${R[OPENCODE_VERSION]} at $(command -v opencode)"

  # Plugin discovery in OpenCode 1.18.30 is glob("{plugin,plugins}/*.{ts,js}")
  # evaluated in the project's .opencode directory. Files directly in
  # .opencode/plugin/ are auto-discovered; a file nested below that is not, so
  # the guard is installed flat and this is asserted below.
  if [ -d "${TARGET}/.opencode/plugin" ]; then
    PLUGIN_DIR="${TARGET}/.opencode/plugin"
    ok "existing plugin directory: .opencode/plugin"
  elif [ -d "${TARGET}/.opencode/plugins" ]; then
    PLUGIN_DIR="${TARGET}/.opencode/plugins"
    ok "existing plugin directory: .opencode/plugins"
  else
    PLUGIN_DIR="${TARGET}/.opencode/plugin"
    ok "will create .opencode/plugin"
  fi
else
  warn "opencode not found on PATH; the guard will be installed but cannot be exercised"
  PLUGIN_DIR="${TARGET}/.opencode/plugin"
fi

# ---------------------------------------------------------------------------
# 3. VERSION-AWARE HANDLING OF AN EXISTING INSTALLATION
# ---------------------------------------------------------------------------
section "3/12  EXISTING INSTALLATION"
ENGINE_DIR="${TARGET}/.verification"
if [ -f "${ENGINE_DIR}/VERSION" ]; then
  R[EXISTING_VERSION]="$(tr -d '[:space:]' < "${ENGINE_DIR}/VERSION")"
  ok "existing installation: ${R[EXISTING_VERSION]}"
  NEWER="$(printf '%s\n%s\n' "${R[EXISTING_VERSION]}" "$BOOTSTRAP_VERSION" | sort -V | tail -1)"
  if [ "$NEWER" = "${R[EXISTING_VERSION]}" ] && [ "${R[EXISTING_VERSION]}" != "$BOOTSTRAP_VERSION" ] && [ "$FORCE" -ne 1 ]; then
    fatal "installed version ${R[EXISTING_VERSION]} is newer than this bootstrap (${BOOTSTRAP_VERSION}).
       Refusing to downgrade. Pass --force to override."
    exit 1
  fi
  BACKUP="${ENGINE_DIR}.backup.$(date +%Y%m%d%H%M%S)"
  if mv "$ENGINE_DIR" "$BACKUP"; then
    ok "previous installation moved to $(basename "$BACKUP")"
  else
    fatal "could not move the existing installation aside"
    exit 1
  fi
else
  ok "no existing installation"
fi

# ---------------------------------------------------------------------------
# 4. INSTALL THE ENGINE
# ---------------------------------------------------------------------------
section "4/12  ENGINE"
mkdir -p "${ENGINE_DIR}/bin" "${ENGINE_DIR}/core" "${ENGINE_DIR}/schema"

cp -R "${SRC_ROOT}/core/cdv" "${ENGINE_DIR}/core/"
mkdir -p "${ENGINE_DIR}/core/templates"
cp "${SRC_ROOT}/templates/verification.yaml" "${ENGINE_DIR}/core/templates/verification.yaml"
cp -R "${SRC_ROOT}/schema/." "${ENGINE_DIR}/schema/"
cp "${SRC_ROOT}/bin/cdv" "${ENGINE_DIR}/bin/cdv"
chmod +x "${ENGINE_DIR}/bin/cdv"
printf '%s\n' "$BOOTSTRAP_VERSION" > "${ENGINE_DIR}/VERSION"
cp "${SRC_ROOT}/CLASSIFICATION" "${ENGINE_DIR}/CLASSIFICATION"

# The installed launcher is the repository launcher with the engine lookup made
# relative to the installation rather than to the source tree.
cat > "${ENGINE_DIR}/bin/cdv" <<'LAUNCHER'
#!/usr/bin/env python3
"""Installed cdv launcher. Generated by the Claim-Driven Verification installer."""

from __future__ import annotations

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
CORE = os.path.join(os.path.dirname(HERE), "core")
sys.path.insert(0, CORE)

from cdv.cli import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
LAUNCHER
chmod +x "${ENGINE_DIR}/bin/cdv"

find "${ENGINE_DIR}/core" -name '__pycache__' -type d -prune -exec rm -rf {} + 2>/dev/null

if "$PYTHON_BIN" "${ENGINE_DIR}/bin/cdv" version >/dev/null 2>&1; then
  ok "engine installed and executable"
  R[ENGINE_STATUS]=INSTALLED
else
  err "engine installed but does not execute"
  R[ENGINE_STATUS]=BROKEN
fi

# ---------------------------------------------------------------------------
# 5. INSTALL THE GUARD
# ---------------------------------------------------------------------------
section "5/12  OPENCODE GUARD"
mkdir -p "$PLUGIN_DIR"
cp "${SRC_ROOT}/runtime/opencode/verification-guard.ts" \
   "${PLUGIN_DIR}/verification-guard.ts"
ok "guard installed at ${PLUGIN_DIR#"$TARGET"/}/verification-guard.ts"

# The guard is auto-discovered only if it sits directly in the plugin directory.
if [ -f "${PLUGIN_DIR}/verification-guard.ts" ]; then
  R[GUARD_STATUS]=INSTALLED
else
  err "guard file is not in place"
  R[GUARD_STATUS]=FAIL
fi

cat > "${ENGINE_DIR}/guard.config.json" <<'GUARDCONFIG'
{
  "guard_disabled": false,
  "selfmod_guard": true,
  "completion_commands": []
}
GUARDCONFIG
ok "guard configuration written"

# Keep run-time noise out of the project's history without touching the shared
# .gitignore, which the project owns.
cat > "${ENGINE_DIR}/.gitignore" <<'GITIGNORE'
audit.log
*.backup.*
GITIGNORE

# ---------------------------------------------------------------------------
# 6. INSTALL THE TEST SUITE (so the installation can be re-verified later)
# ---------------------------------------------------------------------------
section "6/12  SELF-TEST SUITE"
mkdir -p "${ENGINE_DIR}/tests/lib" "${ENGINE_DIR}/tests/e2e"
cp "${SRC_ROOT}/tests/lib/canaries.py" "${ENGINE_DIR}/tests/lib/"
cp "${SRC_ROOT}/tests/lib/canary_runner.py" "${ENGINE_DIR}/tests/lib/"
cp "${SRC_ROOT}/tests/lib/static_checks.py" "${ENGINE_DIR}/tests/lib/"
cp "${SRC_ROOT}/tests/lib/context_canaries.py" "${ENGINE_DIR}/tests/lib/"
cp "${SRC_ROOT}/tests/e2e/enforcement_canary.py" "${ENGINE_DIR}/tests/e2e/"
cp "${SRC_ROOT}/tests/independent_readback.py" "${ENGINE_DIR}/tests/"
ok "canary suite, edge cases and readback tool installed under .verification/tests/"

# ---------------------------------------------------------------------------
# 7. INITIALISE verification.yaml (never overwrite)
# ---------------------------------------------------------------------------
section "7/12  PROJECT VERIFICATION STATE"
DOCUMENT="${TARGET}/verification.yaml"
if [ -f "$DOCUMENT" ]; then
  ok "verification.yaml already exists; left untouched"
  R[DOCUMENT]=PRESERVED
else
  PROJECT_NAME="$(basename "$TARGET")"
  INSTALLED_AT="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  "$PYTHON_BIN" "${ENGINE_DIR}/bin/cdv" init --project "$TARGET" >/dev/null 2>&1
  if [ -f "$DOCUMENT" ]; then
    "$PYTHON_BIN" - "$DOCUMENT" "$PROJECT_NAME" "$INSTALLED_AT" "$BOOTSTRAP_VERSION" <<'STAMP'
import re, sys
path, name, installed_at, version = sys.argv[1:5]
body = open(path, encoding="utf-8").read()
body = body.replace("__PROJECT_NAME__", name)
body = body.replace("__OWNER__", "unset")
body = body.replace("__INSTALLED_AT__", installed_at)
body = body.replace("__BOOTSTRAP_VERSION__", version)
open(path, "w", encoding="utf-8").write(body)
STAMP
    ok "verification.yaml initialised for '${PROJECT_NAME}'"
    R[DOCUMENT]=CREATED
  else
    err "could not create verification.yaml"
    R[DOCUMENT]=FAIL
  fi
fi

# ---------------------------------------------------------------------------
# 8. MANIFEST (enables independent readback)
# ---------------------------------------------------------------------------
section "8/12  INSTALLATION MANIFEST"
"$PYTHON_BIN" - "$TARGET" "$SRC_ROOT" "$BOOTSTRAP_VERSION" "${R[OPENCODE_VERSION]}" <<'MANIFEST'
import hashlib, json, os, sys

target, source, version, opencode_version = sys.argv[1:5]
engine = os.path.join(target, ".verification")


def sha256(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


files: dict[str, str] = {}
for root, dirs, names in os.walk(engine):
    dirs[:] = [d for d in dirs if d != "__pycache__"]
    for name in names:
        path = os.path.join(root, name)
        rel = os.path.relpath(path, engine)
        if rel.startswith("audit.log") or ".backup." in rel:
            continue
        files[rel] = sha256(path)

# The guard lives outside .verification/, so it is recorded separately and
# deliberately not mixed into `files` -- that mapping is engine-relative and a
# project-relative entry in it would resolve against the wrong root.
guard_rel = os.path.join(".opencode", "plugin", "verification-guard.ts")
guard_path = os.path.join(target, guard_rel)

payload = {
    "bootstrap_version": version,
    "schema_version": 1,
    "source_repository_version": version,
    "opencode_version": opencode_version,
    "guard_file": guard_rel,
    "guard_installed_sha256": sha256(guard_path) if os.path.isfile(guard_path) else None,
    "guard_source_sha256": sha256(
        os.path.join(source, "runtime", "opencode", "verification-guard.ts")
    ),
    "engine_launcher": os.path.join(".verification", "bin", "cdv"),
    "files": files,
    "file_count": len(files),
}
with open(os.path.join(engine, "manifest.json"), "w", encoding="utf-8") as handle:
    json.dump(payload, handle, indent=2, sort_keys=True)
print(payload["file_count"])
MANIFEST
FILES="$( "$PYTHON_BIN" -c "
import json,sys
print(json.load(open(sys.argv[1]))['file_count'])
" "${ENGINE_DIR}/manifest.json" 2>/dev/null || echo 0)"
R[FILES_INSTALLED]="$FILES"
ok "manifest written, ${FILES} file(s) hashed"

# ---------------------------------------------------------------------------
# 9. STATIC CHECKS
# ---------------------------------------------------------------------------
section "9/12  STATIC CONSISTENCY CHECKS"
if "$PYTHON_BIN" "${ENGINE_DIR}/tests/lib/static_checks.py" > "${TARGET}/.cdv-static.log" 2>&1; then
  ok "static checks passed"
  R[STATIC_STATUS]=PASS
else
  err "static checks failed"
  sed 's/^/       /' "${TARGET}/.cdv-static.log" | tail -20 >&2
  R[STATIC_STATUS]=FAIL
fi
[ "$VERBOSE" -eq 1 ] && sed 's/^/       /' "${TARGET}/.cdv-static.log"
rm -f "${TARGET}/.cdv-static.log"

# ---------------------------------------------------------------------------
# 10. CANARY SUITE against the *installed* engine
# ---------------------------------------------------------------------------
section "10/12  CANARY SUITE (installed engine)"
if "$PYTHON_BIN" "${ENGINE_DIR}/tests/lib/canary_runner.py" > "${TARGET}/.cdv-canary.log" 2>&1; then
  R[POSITIVE_CANARY]=PASS
  R[NEGATIVE_CANARY]=PASS
  R[CANARY_STATUS]=PASS
  ok "positive canary (complete claim set) => PASS"
  ok "negative canaries (every documented defect) => FAIL as designed"
  # The named gate dimensions are read from the engine's own summary output,
  # computed during the canary run rather than asserted by this script. Each is
  # reported FAIL_AS_DESIGNED: the canary drove that specific dimension to FAIL,
  # which is what shows it is wired to a real check instead of always reporting
  # PASS. A dimension reading NOT_OBSERVED would mean no canary exercised it.
  GATE_DIM="$("$PYTHON_BIN" - "${ENGINE_DIR}/tests/lib" "${ENGINE_DIR}/bin/cdv" <<'DIM'
import os, subprocess, sys, tempfile
import yaml
lib, cdv = sys.argv[1], sys.argv[2]
sys.path.insert(0, lib)
import canaries

# Aggregate the gate dimensions over the negative canaries: each canary is
# expected to drive exactly one dimension to FAIL, which is what shows the
# dimension is wired to a check rather than always reporting PASS.
expected_dimension = {
    "single_evidence_path": "INDEPENDENCE",
    "correlated_evidence": "INDEPENDENCE",
    "unresolved_conflict": "CONFLICT",
    "stale_evidence": "FRESHNESS",
    "unqualified_oracle": "ORACLE_QUALIFICATION",
    "oracle_never_rejected_anything": "ORACLE_QUALIFICATION",
    "missing_residual_uncertainty": "RESIDUAL_UNCERTAINTY",
    "unevidenced_none_known": "RESIDUAL_UNCERTAINTY",
}
results = {}
for name, mutation, _gate, _rule, _sev in canaries.CANARIES:
    if name not in expected_dimension:
        continue
    doc = canaries.build(name, mutation)
    with tempfile.TemporaryDirectory() as work:
        path = os.path.join(work, "verification.yaml")
        yaml.safe_dump(doc, open(path, "w"), sort_keys=False)
        proc = subprocess.run(
            [sys.executable, cdv, "summary", "--project", work],
            capture_output=True, text=True, cwd=work,
        )
    wanted = expected_dimension[name]
    for line in proc.stdout.splitlines():
        if line.startswith("CDV_") and line.endswith("_GATE=FAIL"):
            key = line.split("=", 1)[0]
            dim = key[len("CDV_"):-len("_GATE")]
            if dim == wanted:
                results[dim] = "FAIL_AS_DESIGNED"
    results.setdefault(wanted, "NOT_OBSERVED")
for dim in ["INDEPENDENCE", "ORACLE_QUALIFICATION", "CONFLICT", "FRESHNESS",
            "RESIDUAL_UNCERTAINTY"]:
    print(f"{dim}={results.get(dim, 'NOT_OBSERVED')}")
DIM
)"
  INDEPENDENCE_GATE="$(printf '%s\n' "$GATE_DIM" | sed -n 's/^INDEPENDENCE=//p')"
  ORACLE_GATE="$(printf '%s\n' "$GATE_DIM" | sed -n 's/^ORACLE_QUALIFICATION=//p')"
  CONFLICT_GATE="$(printf '%s\n' "$GATE_DIM" | sed -n 's/^CONFLICT=//p')"
  FRESHNESS_GATE="$(printf '%s\n' "$GATE_DIM" | sed -n 's/^FRESHNESS=//p')"
  RESIDUAL_GATE="$(printf '%s\n' "$GATE_DIM" | sed -n 's/^RESIDUAL_UNCERTAINTY=//p')"
  ok "independence gate: ${INDEPENDENCE_GATE:-NOT_OBSERVED}"
  ok "oracle qualification gate: ${ORACLE_GATE:-NOT_OBSERVED}"
  ok "conflict gate: ${CONFLICT_GATE:-NOT_OBSERVED}"
  ok "freshness gate: ${FRESHNESS_GATE:-NOT_OBSERVED}"
  ok "residual uncertainty gate: ${RESIDUAL_GATE:-NOT_OBSERVED}"
else
  err "canary suite failed"
  sed 's/^/       /' "${TARGET}/.cdv-canary.log" | tail -30 >&2
  R[CANARY_STATUS]=FAIL
  R[POSITIVE_CANARY]=UNKNOWN
  R[NEGATIVE_CANARY]=UNKNOWN
  INDEPENDENCE_GATE=NOT_OBSERVED
  ORACLE_GATE=NOT_OBSERVED
  CONFLICT_GATE=NOT_OBSERVED
  FRESHNESS_GATE=NOT_OBSERVED
  RESIDUAL_GATE=NOT_OBSERVED
fi
[ "$VERBOSE" -eq 1 ] && sed 's/^/       /' "${TARGET}/.cdv-canary.log"
rm -f "${TARGET}/.cdv-canary.log"

# The project's own document must validate too: initialising it and then having
# it fail would mean the installer shipped something it cannot itself satisfy.
if [ -f "$DOCUMENT" ]; then
  if "$PYTHON_BIN" "${ENGINE_DIR}/bin/cdv" gate --project "$TARGET" | grep -q 'CDV_GATE=PASS'; then
    ok "the project's own verification.yaml validates"
    R[SCHEMA_STATUS]=OK
  else
    warn "the project's own verification.yaml does not validate (expected until claims are real)"
    R[SCHEMA_STATUS]=PROJECT_DOCUMENT_NOT_PASSING
  fi
else
  R[SCHEMA_STATUS]=NO_DOCUMENT
fi

# ---------------------------------------------------------------------------
# 11. END-TO-END GUARD ENFORCEMENT CANARY
# ---------------------------------------------------------------------------
section "10b/12  VERIFICATION CONTEXT INTEGRITY"
# Run against the *installed* engine, so this checks the copy the project will
# actually use rather than the source tree it came from.
CONTEXT_LOG="$(mktemp "${TMPDIR:-/tmp}/cdv-ctx-XXXXXX")"
if "$PYTHON_BIN" "${ENGINE_DIR}/tests/lib/context_canaries.py" > "$CONTEXT_LOG" 2>&1; then
  ok "context integrity canaries passed (installed engine)"
  R[CONTEXT_STATUS]=PASS
else
  err "context integrity canaries failed"
  sed 's/^/       /' "$CONTEXT_LOG" | tail -25 >&2
  R[CONTEXT_STATUS]=FAIL
fi
grep -E '^(CONTEXT_|WRONG_|STALE_|GUARD_INACTIVE_)' "$CONTEXT_LOG" | sed 's/^/       /' || true
[ "$VERBOSE" -eq 1 ] && sed 's/^/       /' "$CONTEXT_LOG"
rm -f "$CONTEXT_LOG"

# ---------------------------------------------------------------------------
section "11/12  GUARD ENFORCEMENT CANARY (end to end)"
if [ "$RUN_E2E" -eq 0 ]; then
  warn "--no-e2e given: guard behaviour will NOT be demonstrated; result will be FAIL"
  R[E2E_STATUS]=SKIPPED
elif [ "${R[OPENCODE_RUNTIME]}" != "PRESENT" ]; then
  err "opencode is not available: the guard's actual behaviour cannot be demonstrated"
  R[E2E_STATUS]=UNVERIFIABLE
else
  if [ -z "$E2E_MODEL" ]; then
    # Deterministic discovery, and the choice is printed rather than assumed.
    E2E_MODEL="$(
      opencode models 2>/dev/null | grep -E '^deepseek/' | head -1
    )"
    if [ -z "$E2E_MODEL" ]; then
      E2E_MODEL="$(opencode models 2>/dev/null | head -1)"
    fi
  fi
  if [ -z "$E2E_MODEL" ]; then
    err "no model could be discovered for the enforcement canary; pass --e2e-model"
    R[E2E_STATUS]=UNVERIFIABLE
  else
    ok "canary model: ${E2E_MODEL}"
    TEMP_TARGET="$(mktemp -d "${TMPDIR:-/tmp}/cdv-target-XXXXXX")"
    # The enforcement canary's positive control commits, so the throwaway target
    # needs to be a real repository with a real initial commit.
    git -C "$TEMP_TARGET" init -q 2>/dev/null
    printf '# clean target project for the enforcement canary\n' > "${TEMP_TARGET}/README.md"
    git -C "$TEMP_TARGET" add -A >/dev/null 2>&1
    git -C "$TEMP_TARGET" -c user.email=cdv@invalid -c user.name=cdv \
        commit -qm "initial" >/dev/null 2>&1
    ok "bootstrapping a clean target project at ${TEMP_TARGET}"
    # The nested install gates its own PASS on the E2E, which is deliberately
    # skipped here, so its exit code is non-zero by design and is not inspected.
    # Judging it by exit code would make this step fail every time it worked; it
    # is judged below by the component states it actually reported instead.
    "$0" --no-e2e --quiet "$TEMP_TARGET" > "${TEMP_TARGET}.install.log" 2>&1 || true
    NESTED_LOG="${TEMP_TARGET}.install.log"
    nested_state() { grep -m1 "^$1=" "$NESTED_LOG" 2>/dev/null | cut -d= -f2-; }
    NESTED_STATIC="$(nested_state STATIC_CHECKS)"
    NESTED_CANARY="$(nested_state CANARY_SUITE)"
    NESTED_READBACK="$(nested_state INDEPENDENT_READBACK)"
    NESTED_INSTALLER="$(nested_state INSTALLER_STATUS)"
    if [ "$NESTED_STATIC" = "PASS" ] && [ "$NESTED_CANARY" = "PASS" ] \
       && [ "$NESTED_READBACK" = "PASS" ] && [ "$NESTED_INSTALLER" = "INSTALLED" ]; then
      ok "clean target bootstrapped (engine + canaries + readback all verified there)"
      E2E_ARGS=(--project "$TEMP_TARGET" --model "$E2E_MODEL" --timeout "$E2E_TIMEOUT")
      [ "$VERBOSE" -eq 1 ] && E2E_ARGS+=(--verbose)
      if "$PYTHON_BIN" "${SRC_ROOT}/tests/e2e/enforcement_canary.py" "${E2E_ARGS[@]}"; then
        R[E2E_STATUS]=PASS
        R[REAL_TARGET_BOOTSTRAP]=PASS
      else
        R[E2E_STATUS]=FAIL
        R[REAL_TARGET_BOOTSTRAP]=FAIL
      fi
    else
      err "the clean target project did not bootstrap correctly"
      printf '       installer=%s static=%s canaries=%s readback=%s\n' \
        "${NESTED_INSTALLER:-MISSING}" "${NESTED_STATIC:-MISSING}" \
        "${NESTED_CANARY:-MISSING}" "${NESTED_READBACK:-MISSING}" >&2
      sed 's/^/       /' "$NESTED_LOG" | tail -20 >&2
      R[E2E_STATUS]=FAIL
      R[REAL_TARGET_BOOTSTRAP]=FAIL
    fi
    if [ "$KEEP_TEMP" -eq 1 ]; then
      ok "temporary target kept at ${TEMP_TARGET}"
    else
      rm -rf "$TEMP_TARGET" "${TEMP_TARGET}.install.log"
    fi
  fi
fi

# ---------------------------------------------------------------------------
# 12. INDEPENDENT READBACK
# ---------------------------------------------------------------------------
section "12/12  INDEPENDENT READBACK"
# This step deliberately does not trust anything the installer has reported. It
# re-hashes the installed files against the manifest and against the bootstrap
# source, and re-runs the engine from a fresh process.
if "$PYTHON_BIN" "${SRC_ROOT}/tests/independent_readback.py" \
     --project "$TARGET" --source "$SRC_ROOT" --expect-version "$BOOTSTRAP_VERSION" \
     > "${TARGET}/.cdv-readback.log" 2>&1; then
  R[READBACK_STATUS]=PASS
  ok "installed state independently read back and matches the manifest"
else
  R[READBACK_STATUS]=FAIL
  err "independent readback failed"
  sed 's/^/       /' "${TARGET}/.cdv-readback.log" | tail -25 >&2
fi
[ "$VERBOSE" -eq 1 ] && sed 's/^/       /' "${TARGET}/.cdv-readback.log"
rm -f "${TARGET}/.cdv-readback.log"

# ---------------------------------------------------------------------------
# FINAL REPORT
# ---------------------------------------------------------------------------
BOOTSTRAP_RESULT=FAIL
if [ "${R[ENGINE_STATUS]}" = "INSTALLED" ] \
   && [ "${R[GUARD_STATUS]}" = "INSTALLED" ] \
   && [ "${R[STATIC_STATUS]}" = "PASS" ] \
   && [ "${R[CANARY_STATUS]}" = "PASS" ] \
   && [ "${R[CONTEXT_STATUS]}" = "PASS" ] \
   && [ "${R[E2E_STATUS]}" = "PASS" ] \
   && [ "${R[READBACK_STATUS]}" = "PASS" ]; then
  BOOTSTRAP_RESULT=PASS
fi

# Canonical classifications come from the CLASSIFICATION file, so that the
# installer and tests/verify.sh cannot drift apart or carry a stale release
# label. Fail closed if it is missing or incomplete: a report that guesses its
# own classification is worse than one that refuses to print.
CLASSIFICATION_FILE="${SRC_ROOT}/CLASSIFICATION"
CLASSIFICATION_VERIFIED="$(sed -n 's/^CLASSIFICATION_VERIFIED=//p' "$CLASSIFICATION_FILE" 2>/dev/null | head -1)"
CLASSIFICATION_INCOMPLETE="$(sed -n 's/^CLASSIFICATION_INCOMPLETE=//p' "$CLASSIFICATION_FILE" 2>/dev/null | head -1)"
if [ -z "$CLASSIFICATION_VERIFIED" ] || [ -z "$CLASSIFICATION_INCOMPLETE" ]; then
  fatal "cannot read CLASSIFICATION_VERIFIED and CLASSIFICATION_INCOMPLETE from ${CLASSIFICATION_FILE}"
  exit 3
fi

if [ "$BOOTSTRAP_RESULT" = "PASS" ]; then
  CLASSIFICATION="$CLASSIFICATION_VERIFIED"
else
  CLASSIFICATION="$CLASSIFICATION_INCOMPLETE"
fi

# FINAL_HEAD and WORKTREE describe the bootstrap repository itself and are
# completed by tests/verify.sh; they are reported here so a single installer run
# already yields a complete, honest picture. --quiet --verify keeps a repository
# with no commits from printing the literal string "HEAD" as if it were a sha.
FINAL_HEAD="$(git -C "$SRC_ROOT" rev-parse --quiet --verify HEAD 2>/dev/null | head -1)"
[ -n "$FINAL_HEAD" ] || FINAL_HEAD=NO_COMMIT
WORKTREE_TARGET="$TARGET"

cat <<EOF

$(printf '%s' "$C_BOLD")========================================================================$C_RESET
BOOTSTRAP REPORT
$(printf '%s' "$C_BOLD")========================================================================$C_RESET
FINAL_CLASSIFICATION=${CLASSIFICATION}
BOOTSTRAP_REPOSITORY=claim-driven-verification
BOOTSTRAP_VERSION=${BOOTSTRAP_VERSION}
OPEN_CODE_RUNTIME=${R[OPENCODE_RUNTIME]}:${R[OPENCODE_VERSION]}
PLUGIN_OR_GUARD_STATUS=$([ "${R[E2E_STATUS]}" = "PASS" ] && echo "ACTIVE_AND_ENFORCING" || echo "INSTALLED_UNVERIFIED")
SCHEMA_STATUS=${R[SCHEMA_STATUS]}
INSTALLER_STATUS=${R[ENGINE_STATUS]}
POSITIVE_CANARY=${R[POSITIVE_CANARY]:-UNKNOWN}
NEGATIVE_CANARY=${R[NEGATIVE_CANARY]:-UNKNOWN}
CANARY_SUITE=${R[CANARY_STATUS]}
INDEPENDENCE_GATE=${INDEPENDENCE_GATE:-NOT_OBSERVED}
ORACLE_QUALIFICATION_GATE=${ORACLE_GATE:-NOT_OBSERVED}
CONFLICT_GATE=${CONFLICT_GATE:-NOT_OBSERVED}
FRESHNESS_GATE=${FRESHNESS_GATE:-NOT_OBSERVED}
RESIDUAL_UNCERTAINTY_GATE=${RESIDUAL_GATE:-NOT_OBSERVED}
REAL_TARGET_BOOTSTRAP=${R[REAL_TARGET_BOOTSTRAP]:-NOT_RUN}
INDEPENDENT_READBACK=${R[READBACK_STATUS]}
CONTEXT_INTEGRITY_GATE=${R[CONTEXT_STATUS]}
GUARD_ENFORCEMENT_E2E=${R[E2E_STATUS]}
STATIC_CHECKS=${R[STATIC_STATUS]}
EXISTING_VERSION=${R[EXISTING_VERSION]}
FILES_INSTALLED=${R[FILES_INSTALLED]}
TARGET_PROJECT=${WORKTREE_TARGET}
FINAL_HEAD=${FINAL_HEAD}
$(printf '%s' "$C_BOLD")VERIFICATION_BOOTSTRAP=${BOOTSTRAP_RESULT}$C_RESET
$(printf '%s' "$C_BOLD")========================================================================$C_RESET
EOF

if [ "$BOOTSTRAP_RESULT" != "PASS" ]; then
  cat <<EOF

${C_YELLOW}The bootstrap did not pass. This is the fail-closed outcome, and the reasons
are listed above rather than hidden. Files may well have been installed: that is
not the same as the installation working.

${C_BOLD}To obtain PASS the guard's behaviour must be demonstrated:${C_RESET}
  ${C_DIM}no E2E    -> remove --no-e2e${C_RESET}
  ${C_DIM}no model  -> pass --e2e-model PROVIDER/MODEL${C_RESET}
  ${C_DIM}no opencode -> install OpenCode, or accept that enforcement is unverified${C_RESET}
EOF
  exit 1
fi

exit 0
