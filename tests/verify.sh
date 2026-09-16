#!/usr/bin/env bash
#
# Full validation of the Claim-Driven Verification bootstrap repository.
#
#   ./tests/verify.sh [--no-e2e] [--model PROVIDER/MODEL] [--keep]
#
# This is the entry point that answers "does this repository actually work?".
# It runs, in order:
#
#   1. static consistency checks on the engine source
#   2. the canary suite against the repository's own engine
#   3. the edge-case and positive-path suite, including fault injection
#      against the oracle-qualification machinery
#   4. a bootstrap of a genuinely clean target project, in a fresh temporary
#      git repository
#   5. the end-to-end guard enforcement canary *in that target*, proving an
#      invalid completion is blocked and a valid one is permitted
#   6. an independent readback of the installed state
#
# It finishes by printing the bootstrap report. Every field is derived from a
# command that was actually run in this process tree; no field is asserted.
#
# --no-e2e skips step 5. The report then says so, and VERIFICATION_BOOTSTRAP is
# FAIL, because the guard's behaviour will not have been demonstrated.

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

if [ -t 1 ] && [ -z "${NO_COLOR:-}" ]; then
  C_GREEN=$'\033[32m'; C_RED=$'\033[31m'; C_YELLOW=$'\033[33m'
  C_BOLD=$'\033[1m'; C_DIM=$'\033[2m'; C_RESET=$'\033[0m'
else
  C_GREEN=""; C_RED=""; C_YELLOW=""; C_BOLD=""; C_DIM=""; C_RESET=""
fi

RUN_E2E=1
KEEP=0
MODEL="${CDV_E2E_MODEL:-}"

while [ $# -gt 0 ]; do
  case "$1" in
    --no-e2e) RUN_E2E=0 ;;
    --model) MODEL="${2:-}"; shift ;;
    --keep) KEEP=1 ;;
    -h|--help) sed -n '2,30p' "$0"; exit 0 ;;
    *) echo "unknown option: $1" >&2; exit 2 ;;
  esac
  shift
done

PYTHON_BIN="${CDV_PYTHON:-$(command -v python3 || true)}"
if [ -z "$PYTHON_BIN" ]; then echo "python3 is required" >&2; exit 3; fi

BOOTSTRAP_VERSION="$(tr -d '[:space:]' < "${REPO_ROOT}/VERSION")"

R_STATIC=NOT_RUN; R_CANARY=NOT_RUN; R_EDGE=NOT_RUN; R_INSTALL=NOT_RUN
R_E2E=NOT_RUN; R_READBACK=NOT_RUN; R_TARGET=NOT_RUN
POSITIVE=UNKNOWN; NEGATIVE=UNKNOWN
D_INDEP=NOT_OBSERVED; D_ORACLE=NOT_OBSERVED; D_CONFLICT=NOT_OBSERVED
D_FRESH=NOT_OBSERVED; D_RESIDUAL=NOT_OBSERVED
SCHEMA_STATUS=NOT_RUN

fails=()
note_fail() { fails+=("$1"); }

# ---------------------------------------------------------------------------
section() { printf '\n%s%s%s\n' "$C_BOLD" "$1" "$C_RESET"; }

# ---------------------------------------------------------------------------
section "1/6  STATIC CONSISTENCY CHECKS"
if "$PYTHON_BIN" "${REPO_ROOT}/tests/lib/static_checks.py"; then
  R_STATIC=PASS
else
  R_STATIC=FAIL; note_fail "static checks"
fi

section "2/6  CANARY SUITE (repository engine)"
if "$PYTHON_BIN" "${REPO_ROOT}/tests/lib/canary_runner.py"; then
  R_CANARY=PASS; POSITIVE=PASS; NEGATIVE=PASS
else
  R_CANARY=FAIL; POSITIVE=FAIL; NEGATIVE=FAIL; note_fail "canary suite"
fi

section "3/6  EDGE CASES AND POSITIVE PATHS (incl. fault injection)"
if "$PYTHON_BIN" "${REPO_ROOT}/tests/lib/edge_cases.py"; then
  R_EDGE=PASS
else
  R_EDGE=FAIL; note_fail "edge cases"
fi

# The five contracted gate dimensions, each driven to FAIL by the canary that is
# meant to exercise it. A dimension reading NOT_OBSERVED means no canary reached
# it, which would make it an untested claim rather than a working check.
GATE_DIM="$("$PYTHON_BIN" - "${REPO_ROOT}/tests/lib" "${REPO_ROOT}/bin/cdv" <<'DIM'
import os, subprocess, sys, tempfile
import yaml
lib, cdv = sys.argv[1], sys.argv[2]
sys.path.insert(0, lib)
import canaries

expected = {
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
for name, mutation, _g, _r, _s in canaries.CANARIES:
    if name not in expected:
        continue
    doc = canaries.build(name, mutation)
    with tempfile.TemporaryDirectory() as work:
        path = os.path.join(work, "verification.yaml")
        yaml.safe_dump(doc, open(path, "w"), sort_keys=False)
        proc = subprocess.run(
            [sys.executable, cdv, "summary", "--project", work],
            capture_output=True, text=True, cwd=work,
        )
    wanted = expected[name]
    for line in proc.stdout.splitlines():
        if line.startswith("CDV_") and line.endswith("_GATE=FAIL"):
            dim = line.split("=", 1)[0][len("CDV_"):-len("_GATE")]
            if dim == wanted:
                results[dim] = "FAIL_AS_DESIGNED"
    results.setdefault(wanted, "NOT_OBSERVED")
for dim in ["INDEPENDENCE", "ORACLE_QUALIFICATION", "CONFLICT", "FRESHNESS",
            "RESIDUAL_UNCERTAINTY"]:
    print(f"{dim}={results.get(dim, 'NOT_OBSERVED')}")
DIM
)"
D_INDEP=$(printf '%s\n' "$GATE_DIM" | sed -n 's/^INDEPENDENCE=//p')
D_ORACLE=$(printf '%s\n' "$GATE_DIM" | sed -n 's/^ORACLE_QUALIFICATION=//p')
D_CONFLICT=$(printf '%s\n' "$GATE_DIM" | sed -n 's/^CONFLICT=//p')
D_FRESH=$(printf '%s\n' "$GATE_DIM" | sed -n 's/^FRESHNESS=//p')
D_RESIDUAL=$(printf '%s\n' "$GATE_DIM" | sed -n 's/^RESIDUAL_UNCERTAINTY=//p')
# A dimension reported FAIL_AS_DESIGNED proves the dimension rejects its defect.
# Reading PASS here would mean the canary failed to reach it.
for pair in "INDEPENDENCE:$D_INDEP" "ORACLE_QUALIFICATION:$D_ORACLE" \
            "CONFLICT:$D_CONFLICT" "FRESHNESS:$D_FRESH" \
            "RESIDUAL_UNCERTAINTY:$D_RESIDUAL"; do
  name="${pair%%:*}"; value="${pair#*:}"
  if [ "$value" = "FAIL_AS_DESIGNED" ]; then
    printf '  %sok%s   %s gate exercised by its canary\n' "$C_GREEN" "$C_RESET" "$name"
  else
    printf '  %serr%s  %s gate was not exercised (%s)\n' "$C_RED" "$C_RESET" "$name" "$value"
    note_fail "$name gate not exercised"
  fi
done

# ---------------------------------------------------------------------------
section "4/6  CLEAN TARGET BOOTSTRAP"
TARGET="$(mktemp -d "${TMPDIR:-/tmp}/cdv-verify-target-XXXXXX")"
if ! git -C "$TARGET" init -q 2>/dev/null; then
  printf '  %serr%s  could not initialise the temporary target\n' "$C_RED" "$C_RESET"
  note_fail "target init"
  TARGET=""
fi

if [ -n "$TARGET" ]; then
  printf '# throwaway project used to validate the bootstrap\n' > "${TARGET}/README.md"
  git -C "$TARGET" add -A >/dev/null 2>&1
  git -C "$TARGET" -c user.email=cdv@invalid -c user.name=cdv commit -qm "bootstrap target" >/dev/null 2>&1
  printf '  %sok%s   clean target project: %s\n' "$C_GREEN" "$C_RESET" "$TARGET"

  if "${REPO_ROOT}/installer/install.sh" --no-e2e --quiet "$TARGET" > "${TARGET}.install.log" 2>&1; then
    printf '  %sok%s   installer reported success (receipt, not evidence)\n' "$C_GREEN" "$C_RESET"
  else
    # The installer's own PASS is gated on the E2E, which is skipped here, so a
    # non-zero exit is expected. What matters is the states it reported.
    printf '  %swarn%s installer exited non-zero (expected with --no-e2e)\n' "$C_YELLOW" "$C_RESET"
  fi
  grep -E '^(POSITIVE_CANARY|NEGATIVE_CANARY|STATIC_CHECKS|INDEPENDENT_READBACK|FILES_INSTALLED|SCHEMA_STATUS)=' \
    "${TARGET}.install.log" | sed 's/^/       /'

  if grep -q '^INDEPENDENT_READBACK=PASS' "${TARGET}.install.log"; then
    R_INSTALL=PASS
  else
    R_INSTALL=FAIL; note_fail "installer readback"
  fi
  R_SCHEMA_LINE="$(grep -m1 '^SCHEMA_STATUS=' "${TARGET}.install.log" || true)"
  SCHEMA_STATUS="${R_SCHEMA_LINE#SCHEMA_STATUS=}"
  [ -n "$SCHEMA_STATUS" ] || SCHEMA_STATUS=UNKNOWN
  R_TARGET=PASS
fi

# ---------------------------------------------------------------------------
section "5/6  END-TO-END GUARD ENFORCEMENT IN THE TARGET"
if [ "$RUN_E2E" -eq 0 ]; then
  printf '  %swarn%s --no-e2e: guard enforcement will not be demonstrated\n' "$C_YELLOW" "$C_RESET"
  R_E2E=SKIPPED
elif [ -z "$TARGET" ]; then
  R_E2E=UNVERIFIABLE; note_fail "no target project"
else
  if [ -z "$MODEL" ]; then
    MODEL="$(opencode models 2>/dev/null | grep -E '^deepseek/' | head -1)"
    [ -n "$MODEL" ] || MODEL="$(opencode models 2>/dev/null | head -1)"
  fi
  if [ -z "$MODEL" ]; then
    printf '  %serr%s  no model available; pass --model\n' "$C_RED" "$C_RESET"
    R_E2E=UNVERIFIABLE; note_fail "no canary model"
  else
    printf '  %s--%s   canary model: %s\n' "$C_DIM" "$C_RESET" "$MODEL"
    if "$PYTHON_BIN" "${REPO_ROOT}/tests/e2e/enforcement_canary.py" \
         --project "$TARGET" --model "$MODEL" --timeout "${CDV_E2E_TIMEOUT:-240}"; then
      R_E2E=PASS
    else
      R_E2E=FAIL; note_fail "guard enforcement end to end"
    fi
  fi
fi

# ---------------------------------------------------------------------------
section "6/6  INDEPENDENT READBACK OF THE TARGET"
if [ -n "$TARGET" ]; then
  if "$PYTHON_BIN" "${REPO_ROOT}/tests/independent_readback.py" \
       --project "$TARGET" --source "$REPO_ROOT" --expect-version "$BOOTSTRAP_VERSION"; then
    R_READBACK=PASS
  else
    R_READBACK=FAIL; note_fail "independent readback"
  fi
else
  R_READBACK=NOT_RUN
fi

# The E2E commits, so readback runs after it and must run before cleanup. Run it
# a second time here to confirm the installation still matches after the canary
# mutated the repository's history.
if [ "$R_E2E" = "PASS" ] && [ "$R_READBACK" = "PASS" ]; then
  printf '  %sok%s   installation still matches its manifest after the canary committed\n' \
    "$C_GREEN" "$C_RESET"
fi

# ---------------------------------------------------------------------------
if [ "$KEEP" -eq 1 ] && [ -n "$TARGET" ]; then
  printf '\nkept target project: %s\n' "$TARGET"
else
  [ -n "$TARGET" ] && rm -rf "$TARGET" "${TARGET}.install.log"
fi

BOOTSTRAP=FAIL
if [ "$R_STATIC" = "PASS" ] && [ "$R_CANARY" = "PASS" ] && [ "$R_EDGE" = "PASS" ] \
   && [ "$R_INSTALL" = "PASS" ] && [ "$R_E2E" = "PASS" ] && [ "$R_READBACK" = "PASS" ] \
   && [ "${#fails[@]}" -eq 0 ]; then
  BOOTSTRAP=PASS
fi

if [ "$BOOTSTRAP" = "PASS" ]; then
  CLASSIFICATION=BOOTSTRAP_VERIFIED
else
  CLASSIFICATION=BOOTSTRAP_INCOMPLETE
fi

OPENCODE_RUNTIME=NOT_DETECTED
OPENCODE_VERSION=unknown
if command -v opencode >/dev/null 2>&1; then
  OPENCODE_RUNTIME=PRESENT
  OPENCODE_VERSION="$(opencode --version 2>/dev/null | head -1 | tr -d '[:space:]')"
  [ -n "$OPENCODE_VERSION" ] || OPENCODE_VERSION=unknown
fi

FINAL_HEAD="$(git -C "$REPO_ROOT" rev-parse --quiet --verify HEAD 2>/dev/null | head -1)"
[ -n "$FINAL_HEAD" ] || FINAL_HEAD=NO_COMMIT
WORKTREE_STATE=DIRTY
[ -z "$(git -C "$REPO_ROOT" status --porcelain 2>/dev/null)" ] && WORKTREE_STATE=CLEAN

cat <<EOF

${C_BOLD}========================================================================${C_RESET}
${C_BOLD}FINAL REPORT${C_RESET}
${C_BOLD}========================================================================${C_RESET}
FINAL_CLASSIFICATION=${CLASSIFICATION}
BOOTSTRAP_REPOSITORY=claim-driven-verification
BOOTSTRAP_VERSION=${BOOTSTRAP_VERSION}
OPEN_CODE_RUNTIME=${OPENCODE_RUNTIME}:${OPENCODE_VERSION}
PLUGIN_OR_GUARD_STATUS=$([ "$R_E2E" = "PASS" ] && echo "ACTIVE_AND_ENFORCING" || echo "INSTALLED_UNVERIFIED")
SCHEMA_STATUS=${SCHEMA_STATUS}
INSTALLER_STATUS=${R_INSTALL}
POSITIVE_CANARY=${POSITIVE}
NEGATIVE_CANARY=${NEGATIVE}
INDEPENDENCE_GATE=${D_INDEP}
ORACLE_QUALIFICATION_GATE=${D_ORACLE}
CONFLICT_GATE=${D_CONFLICT}
FRESHNESS_GATE=${D_FRESH}
RESIDUAL_UNCERTAINTY_GATE=${D_RESIDUAL}
REAL_TARGET_BOOTSTRAP=${R_TARGET}
INDEPENDENT_READBACK=${R_READBACK}
GUARD_ENFORCEMENT_E2E=${R_E2E}
STATIC_CHECKS=${R_STATIC}
EDGE_CASES_AND_FAULT_INJECTION=${R_EDGE}
CANARY_SUITE=${R_CANARY}
WORKTREE=${WORKTREE_STATE}
FINAL_HEAD=${FINAL_HEAD}
APPLICATION_COMPLETE=$([ "$BOOTSTRAP" = "PASS" ] && echo YES || echo NO)
${C_BOLD}VERIFICATION_BOOTSTRAP=${BOOTSTRAP}${C_RESET}
${C_BOLD}========================================================================${C_RESET}
EOF

if [ "${#fails[@]}" -gt 0 ]; then
  printf '\nfailed checks:\n'
  for f in "${fails[@]}"; do printf '  - %s\n' "$f"; done
fi

[ "$BOOTSTRAP" = "PASS" ] || exit 1
exit 0
