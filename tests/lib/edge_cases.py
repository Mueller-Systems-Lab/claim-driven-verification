#!/usr/bin/env python3
"""Edge-case and positive-path tests.

The canary suite (`canary_runner.py`) proves the gate *rejects* defects. This
suite covers what that leaves open:

  A. INPUT HANDLING -- malformed YAML, unsupported schema version, a non-mapping
     root, an unknown top-level key, a missing file. These must produce a
     specific rule id and a non-zero exit, never a traceback and never a PASS.

  B. POSITIVE PATHS -- the mechanisms that can *unblock* a claim must be shown to
     work, not merely to exist. If the only tested behaviour of a documented
     exception, a conflict resolution, or a temporal check is that it blocks when
     absent, then "always blocks" would pass the suite. So each waiver has a case
     where it is correctly applied and the claim passes.

  C. EXECUTABLE ORACLE QUALIFICATION -- fault injection against the verification
     machinery itself. A project-supplied oracle is run against a known-good and
     a deliberately corrupt input; the corrupt case is the load-bearing one. The
     suite includes an oracle that FAILS to detect corruption, and asserts that
     the engine refuses to qualify it. That is the verifier-of-the-verifier
     check: a verifier that has never been shown to reject a known bad result is
     not evidence, and the engine must act on that.

Exit code 0 only when every case behaves as specified.
"""

from __future__ import annotations

import copy
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
CDV = os.path.join(REPO, "bin", "cdv")

sys.path.insert(0, HERE)
import canaries  # noqa: E402

try:
    import yaml
except ImportError:  # pragma: no cover
    print("FAIL: PyYAML is required", file=sys.stderr)
    sys.exit(3)

GREEN = "\033[32m"
RED = "\033[31m"
RESET = "\033[0m"

failures: list[str] = []
case_labels: list[str] = []


def report(ok: bool, label: str, detail: str = "") -> bool:
    case_labels.append(label)
    mark = f"{GREEN}ok{RESET}" if ok else f"{RED}FAIL{RESET}"
    print(f"  {mark}   {label}" + (f"\n         {detail}" if detail and not ok else ""))
    if not ok:
        failures.append(label)
    return ok


def invoke(
    workdir: str,
    *extra: str,
    document: str | None = None,
    raw: str | None = None,
    allow_execute: bool = False,
) -> tuple[int, str, str, dict]:
    """Run the engine. Returns (exit_code, stdout, stderr, parsed_json)."""
    path = os.path.join(workdir, "verification.yaml")
    if raw is not None:
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(raw)
    elif document is not None:
        with open(path, "w", encoding="utf-8") as handle:
            yaml.safe_dump(document, handle, sort_keys=False)
    elif not os.path.isfile(path):
        path = os.path.join(workdir, "does-not-exist.yaml")

    cmd = [sys.executable, CDV, "validate", "--json", path]
    if allow_execute:
        cmd.append("--allow-execute-oracles")
    proc = subprocess.run(cmd, capture_output=True, text=True, cwd=workdir, timeout=180)
    payload: dict = {}
    if proc.stdout.strip().startswith("{"):
        try:
            payload = json.loads(proc.stdout)
        except json.JSONDecodeError:
            payload = {}
    return proc.returncode, proc.stdout, proc.stderr, payload


def rules_in(payload: dict) -> set[str]:
    out = set()
    for finding in payload.get("document_findings", []) or []:
        out.add(finding.get("rule", ""))
    for claim in payload.get("claims", []) or []:
        for finding in claim.get("findings", []) or []:
            out.add(finding.get("rule", ""))
    return out


def gate_of(payload: dict) -> str:
    return payload.get("gate", "NO_JSON")


# ---------------------------------------------------------------------------
# A. Input handling
# ---------------------------------------------------------------------------


def test_input_handling() -> None:
    print("\nA. input handling (must never PASS, must never trace back)")

    cases: list[tuple[str, str | None, dict | None, str, bool]] = [
        (
            "malformed YAML is rejected",
            "version: 1\nclaims:\n  - id: X\n   statement: bad indentation\n",
            None,
            "YAML_MALFORMED",
            False,
        ),
        (
            "unsupported schema version is rejected",
            None,
            {"version": 99, "claims": []},
            "SCHEMA_VERSION_UNSUPPORTED",
            False,
        ),
        (
            "missing version key is rejected",
            None,
            {"claims": []},
            "SCHEMA_VERSION_MISSING",
            False,
        ),
        (
            "non-mapping root is rejected",
            "- just\n- a\n- list\n",
            None,
            "ROOT_NOT_MAPPING",
            False,
        ),
        (
            "no document at all is rejected",
            None,
            None,
            "INPUT_FILE_MISSING",
            False,
        ),
        (
            "unknown top-level key fails the gate (typo cannot be silently ignored)",
            None,
            {
                "version": 1,
                "claims": [],
                "state": {"commit": "X"},
                # A typo for `oracles`: if this were ignored, every oracle
                # reference would silently become undefined.
                "oracle": {"fs": {"kind": "independent-technical"}},
            },
            "UNKNOWN_TOP_LEVEL_KEY",
            False,
        ),
        (
            "duplicate claim ids are rejected",
            None,
            {
                "version": 1,
                "state": {"commit": "X"},
                "claims": [
                    {"id": "DUP", "statement": "a", "type": "STATE", "criticality": "NON_CRITICAL",
                     "criticality_rationale": "x" * 90},
                    {"id": "DUP", "statement": "b", "type": "STATE", "criticality": "NON_CRITICAL",
                     "criticality_rationale": "x" * 90},
                ],
            },
            "DUPLICATE_CLAIM_ID",
            False,
        ),
    ]

    for label, raw, document, expected_rule, _ in cases:
        with tempfile.TemporaryDirectory() as work:
            code, stdout, stderr, payload = invoke(work, raw=raw, document=document)
        combined = stdout + stderr
        seen = rules_in(payload)
        ok = (
            (expected_rule in seen or expected_rule in combined)
            and code != 0
            and "Traceback" not in combined
        )
        report(ok, label, f"exit={code} rules={sorted(seen)} stderr={stderr.strip()[:200]}")

    # A document with no claims is structurally valid but must not report a
    # vacuous PASS for the named gate dimensions.
    with tempfile.TemporaryDirectory() as work:
        code, stdout, stderr, payload = invoke(work, document={"version": 1, "claims": []})
    dims = payload.get("dimensions", {})
    report(
        code == 0 and all(v == "NOT_EXERCISED" for v in dims.values()),
        "a document with no claims reports NOT_EXERCISED, not a vacuous PASS",
        f"exit={code} dimensions={dims}",
    )


# ---------------------------------------------------------------------------
# B. Positive paths for the waivers
# ---------------------------------------------------------------------------


def _good_document() -> dict:
    return canaries.build("baseline_positive", None)


def test_positive_paths() -> None:
    print("\nB. documented waivers work when correctly invoked")

    # B1. A properly documented independence exception is accepted, and recorded.
    doc = _good_document()
    claim = doc["claims"][0]
    claim["failure_modes"] = [claim["failure_modes"][0]]
    claim["evidence"] = [claim["evidence"][0]]
    doc["independence_exceptions"] = [
        {
            "claim_id": "PDF-EXPORT-001",
            "justification": (
                "The second path requires a licensed parser unavailable in CI; the "
                "gap is tracked in issue 4711 and re-checked before each release."
            ),
            "accepted_by": "release-owner",
            "accepted_at": "2026-01-15",
        }
    ]
    with tempfile.TemporaryDirectory() as work:
        code, stdout, stderr, payload = invoke(work, document=doc)
    seen = rules_in(payload)
    report(
        code == 0 and gate_of(payload) == "PASS" and "INSUFFICIENT_INDEPENDENT_PATHS" in seen,
        "a documented independence exception permits PASS and is recorded as a warning",
        f"exit={code} gate={gate_of(payload)} rules={sorted(seen)}",
    )

    # B2. A conflict resolved with re-run evidence is accepted.
    doc = canaries.build("unresolved_conflict", canaries.mutate_unresolved_conflict)
    claim = doc["claims"][0]
    claim["evidence"].append(
        {
            "id": "EV-004",
            "oracle": "filesystem-readback",
            "result": "PASS",
            "failure_modes": ["NO_FILE_CREATED"],
            "dimensions": {
                "model": "none",
                "tool": "coreutils",
                "implementation": "filesystem-readback",
                "runtime": "posix-shell",
                "data_source": "output-directory",
                "observation_channel": "filesystem",
                "specification_source": "filesystem-semantics",
            },
            "observed_state": {"commit": "TESTCOMMIT", "tree_hash": "TESTTREE"},
        }
    )
    doc["conflicts"] = [
        {
            "id": "C-1",
            "claim_id": "PDF-EXPORT-001",
            "between": ["EV-001", "EV-003"],
            "failure_mode": "NO_FILE_CREATED",
            "status": "RESOLVED",
            "root_cause": "EV-003 ran against a stale output directory left by a prior test.",
            "correction": "The test fixture now clears the output directory before running.",
            "re_run_evidence": "EV-004",
            "resolved_by": "ci-owner",
            "resolved_at": "2026-01-15",
        }
    ]
    with tempfile.TemporaryDirectory() as work:
        code, stdout, stderr, payload = invoke(work, document=doc)
    seen = rules_in(payload)
    report(
        code == 0 and gate_of(payload) == "PASS" and "EVIDENCE_CONFLICT_UNRESOLVED" not in seen,
        "a conflict resolved with root cause and re-run evidence is accepted",
        f"exit={code} gate={gate_of(payload)} rules={sorted(seen)}",
    )

    # B3. A temporal claim with both anchors passes.
    doc = canaries.build("temporal_without_durability", canaries.mutate_temporal_without_durability)
    doc["claims"][0]["temporal_checks"] = [
        {"kind": "repeated-execution", "result": "PASS"},
        {"kind": "application-restart", "result": "PASS"},
    ]
    with tempfile.TemporaryDirectory() as work:
        code, stdout, stderr, payload = invoke(work, document=doc)
    seen = rules_in(payload)
    report(
        code == 0 and gate_of(payload) == "PASS" and "TEMPORAL_CHECKS_MISSING" not in seen,
        "a temporal claim with a repetition anchor and a durability anchor passes",
        f"exit={code} gate={gate_of(payload)} rules={sorted(seen)}",
    )

    # B4. An OUTCOME claim passes when an external-reality oracle observes the
    #     result outside the producing application, and two materially
    #     independent paths through qualified external oracles support it.
    doc = _good_document()
    doc["oracles"]["rendered-page-check"] = {
        "kind": "cross-modal",
        "description": "render the artifact to an image and inspect what is visible",
        "qualification": {
            "mode": "declared",
            "rationale": (
                "This test constructs the oracle inline and runs on machines where a "
                "rendering stack is not guaranteed to be present."
            ),
            "executable_unavailable_because": (
                "There is no artifact in this synthetic document for a renderer to "
                "rasterise, so no executed case can be constructed."
            ),
            "positive_case": {"description": "page renders", "result": "PASS"},
            "negative_case": {"description": "blank page rejected", "result": "DETECTED"},
        },
    }
    claim = doc["claims"][0]
    claim["statement"] = "The user can open the exported PDF outside the application."
    claim["type"] = "OUTCOME"
    claim["failure_modes"] = [
        {"id": "CANNOT_BE_OPENED_EXTERNALLY", "oracle": "external-pdf-parser"},
        {"id": "VISIBLE_CONTENT_WRONG", "oracle": "rendered-page-check"},
    ]
    claim["evidence"][0]["failure_modes"] = ["CANNOT_BE_OPENED_EXTERNALLY"]
    claim["evidence"][1]["failure_modes"] = ["CANNOT_BE_OPENED_EXTERNALLY"]
    claim["evidence"].append(
        {
            "id": "EV-003",
            "oracle": "rendered-page-check",
            "result": "PASS",
            "failure_modes": ["VISIBLE_CONTENT_WRONG"],
            "dimensions": {
                "model": "vision-model-a",
                "tool": "renderer",
                "implementation": "page-rasteriser",
                "runtime": "cpython",
                "data_source": "rendered-png",
                "observation_channel": "visual",
                "specification_source": "human-readable-layout-spec",
            },
            "observed_state": {"commit": "TESTCOMMIT", "tree_hash": "TESTTREE"},
        }
    )
    with tempfile.TemporaryDirectory() as work:
        code, stdout, stderr, payload = invoke(work, document=doc)
    seen = rules_in(payload)
    report(
        code == 0 and gate_of(payload) == "PASS",
        "an OUTCOME claim verified by external-reality and cross-modal oracles passes",
        f"exit={code} gate={gate_of(payload)} rules={sorted(seen)} "
        f"blocking={[f.get('rule') for f in (payload.get('claims') or [{}])[0].get('findings', []) if f.get('severity') == 'ERROR']}",
    )

    # B5. A reviewed criticality downgrade is accepted but still gated as critical.
    doc = _good_document()
    claim = doc["claims"][0]
    claim["criticality"] = "NON_CRITICAL"
    claim["criticality_rationale"] = (
        "Reviewed with the release owner: the export path is exercised only by an "
        "internal reporting tool and the artifact never leaves the local machine."
    )
    doc["criticality_exceptions"] = [
        {
            "claim_id": "PDF-EXPORT-001",
            "justification": (
                "Confirmed with the release owner that no external consumer depends on "
                "this artifact, so the critical evidence bar is not proportionate."
            ),
            "reviewed_by": "release-owner",
            "reviewed_at": "2026-01-15",
        }
    ]
    with tempfile.TemporaryDirectory() as work:
        code, stdout, stderr, payload = invoke(work, document=doc)
    seen = rules_in(payload)
    claim_payload = (payload.get("claims") or [{}])[0]
    report(
        gate_of(payload) == "PASS"
        and "CRITICALITY_DOWNGRADE_BLOCKED" in seen
        and claim_payload.get("effective_criticality") == "CRITICAL",
        "a reviewed downgrade passes but is still evaluated with the critical rule set",
        f"gate={gate_of(payload)} rules={sorted(seen)} "
        f"effective={claim_payload.get('effective_criticality')}",
    )


# ---------------------------------------------------------------------------
# C. Executable oracle qualification (fault injection)
# ---------------------------------------------------------------------------

GOOD_ORACLE_CMD = "printf 'CDV_ORACLE_POSITIVE=PASS\\n'"
GOOD_NEGATIVE_CMD = "printf 'CDV_ORACLE_NEGATIVE=DETECTED\\n'"
# An oracle that asserts success no matter what. This is the corrupt verifier.
BLIND_NEGATIVE_CMD = "printf 'CDV_ORACLE_NEGATIVE=NOT_DETECTED\\n'"
CRASHING_CMD = "printf 'CDV_ORACLE_NEGATIVE=DETECTED\\n'; exit 7"


def _oracle_document(negative_command: str, *, mode: str = "executable") -> dict:
    doc = _good_document()
    doc["oracles"]["filesystem-readback"]["qualification"] = {
        "mode": mode,
        "positive_command": GOOD_ORACLE_CMD,
        "negative_command": negative_command,
    }
    return doc


def test_executable_oracles() -> None:
    print("\nC. executable oracle qualification (fault injection on the verifier itself)")

    # C1. Without permission to execute, an executable oracle is UNVERIFIED and
    #     blocks the critical claim. Fail-closed, not silently trusted.
    with tempfile.TemporaryDirectory() as work:
        code, stdout, stderr, payload = invoke(
            work, document=_oracle_document(GOOD_NEGATIVE_CMD), allow_execute=False
        )
    seen = rules_in(payload)
    report(
        code != 0 and "ORACLE_QUALIFICATION_UNVERIFIED" in seen,
        "an executable oracle is NOT trusted when oracle execution is not permitted",
        f"exit={code} rules={sorted(seen)}",
    )

    # C2. With permission, the oracle is actually run. Positive and negative
    #     markers are observed, so the oracle is certified.
    with tempfile.TemporaryDirectory() as work:
        code, stdout, stderr, payload = invoke(
            work, document=_oracle_document(GOOD_NEGATIVE_CMD), allow_execute=True
        )
    seen = rules_in(payload)
    report(
        code == 0 and gate_of(payload) == "PASS" and "ORACLE_QUALIFICATION_EXECUTED" in seen,
        "a sound oracle is certified by executing both its known-good and known-bad cases",
        f"exit={code} gate={gate_of(payload)} rules={sorted(seen)}",
    )

    # C3. FAULT INJECTION. The oracle never rejects anything. The engine must
    #     refuse to qualify it, so the claim cannot pass. This is the case that
    #     justifies the whole oracle-qualification requirement.
    with tempfile.TemporaryDirectory() as work:
        code, stdout, stderr, payload = invoke(
            work, document=_oracle_document(BLIND_NEGATIVE_CMD), allow_execute=True
        )
    seen = rules_in(payload)
    report(
        code != 0 and "ORACLE_QUALIFICATION_BLOCKED" in seen,
        "an oracle that never rejects a known-bad input is REFUSED qualification",
        f"exit={code} rules={sorted(seen)}",
    )

    # C4. An oracle whose negative case crashes must also be refused: a
    #     non-zero exit is not a detection.
    with tempfile.TemporaryDirectory() as work:
        code, stdout, stderr, payload = invoke(
            work, document=_oracle_document(CRASHING_CMD), allow_execute=True
        )
    seen = rules_in(payload)
    report(
        code != 0 and "ORACLE_QUALIFICATION_BLOCKED" in seen,
        "an oracle that crashes rather than reporting is refused qualification",
        f"exit={code} rules={sorted(seen)}",
    )


DECLARED_REASON = (
    "The project declares the qualifying cases because executing them requires "
    "tooling that is not guaranteed to be present in the validation environment."
)
DECLARED_INFEASIBLE = (
    "Executing this oracle would require an external tool that cannot be assumed "
    "available, so a known-good and known-bad pair cannot be constructed here."
)


def _declared_qualification(**overrides) -> dict:
    qual = {
        "mode": "declared",
        "rationale": DECLARED_REASON,
        "executable_unavailable_because": DECLARED_INFEASIBLE,
        "positive_case": {"description": "known good accepted", "result": "PASS"},
        "negative_case": {"description": "known bad rejected", "result": "DETECTED"},
    }
    qual.update(overrides)
    return qual


def _with_all_oracles(qualification: dict) -> dict:
    """Apply one qualification block to every oracle a critical claim uses.

    Needed for the claim-level assurance, which is DECLARED only when *every*
    oracle carrying the claim is declared.
    """
    doc = _good_document()
    for name in list(doc["oracles"]):
        doc["oracles"][name]["qualification"] = copy.deepcopy(qualification)
    return doc


def test_qualification_assurance() -> None:
    print("\nC2. qualification assurance: EXECUTED versus DECLARED")

    # Every oracle declared and properly documented: the claim may pass, but the
    # assurance must stay visibly weaker than an executed qualification.
    with tempfile.TemporaryDirectory() as work:
        code, stdout, stderr, payload = invoke(
            work, document=_with_all_oracles(_declared_qualification())
        )
    seen = rules_in(payload)
    claim_payload = (payload.get("claims") or [{}])[0]
    report(
        code == 0
        and gate_of(payload) == "PASS"
        and claim_payload.get("oracle_assurance") == "DECLARED"
        and "ORACLE_ASSURANCE_DEGRADED" in seen,
        "a documented DECLARED claim passes but its assurance is recorded as DECLARED",
        f"exit={code} gate={gate_of(payload)} "
        f"assurance={claim_payload.get('oracle_assurance')} rules={sorted(seen)}",
    )

    # The distinction must be machine-readable, not just prose in a message.
    with tempfile.TemporaryDirectory() as work:
        path = os.path.join(work, "verification.yaml")
        with open(path, "w", encoding="utf-8") as handle:
            yaml.safe_dump(
                _with_all_oracles(_declared_qualification()), handle, sort_keys=False
            )
        proc = subprocess.run(
            [sys.executable, CDV, "summary", "--project", work],
            capture_output=True, text=True, cwd=work, timeout=120,
        )
    report(
        "CDV_ORACLE_QUALIFICATION_MODE=DECLARED" in proc.stdout,
        "the qualification mode is machine-readable in the summary",
        f"stdout={proc.stdout[:400]}",
    )

    # No statement of why executing is impossible. This is the load-bearing case
    # for the preferred policy: a critical claim may not lean on a declared
    # oracle unless executing it is genuinely unavailable, and that must be said.
    with tempfile.TemporaryDirectory() as work:
        qual = _declared_qualification()
        del qual["executable_unavailable_because"]
        code, stdout, stderr, payload = invoke(work, document=_with_all_oracles(qual))
    seen = rules_in(payload)
    report(
        code != 0 and "ORACLE_DECLARED_MISSING_FEASIBILITY" in seen,
        "DECLARED without stating why executing is unavailable is rejected",
        f"exit={code} rules={sorted(seen)}",
    )

    with tempfile.TemporaryDirectory() as work:
        qual = _declared_qualification()
        del qual["rationale"]
        code, stdout, stderr, payload = invoke(work, document=_with_all_oracles(qual))
    seen = rules_in(payload)
    report(
        code != 0 and "ORACLE_DECLARED_MISSING_RATIONALE" in seen,
        "DECLARED without a rationale is rejected",
        f"exit={code} rules={sorted(seen)}",
    )

    # Admitting execution was possible while declaring anyway is refusing
    # stronger evidence the project could have produced.
    with tempfile.TemporaryDirectory() as work:
        doc = _with_all_oracles(_declared_qualification(executable_feasible=True))
        code, stdout, stderr, payload = invoke(work, document=doc)
    seen = rules_in(payload)
    report(
        code != 0 and "ORACLE_EXECUTABLE_FEASIBLE_BUT_DECLARED" in seen,
        "DECLARED while execution was feasible is rejected",
        f"exit={code} rules={sorted(seen)}",
    )

    # Masquerade: claiming EXECUTED with nothing to execute. Without this rule a
    # document could obtain the stronger assurance by relabelling.
    with tempfile.TemporaryDirectory() as work:
        qual = _declared_qualification(mode="EXECUTED")
        code, stdout, stderr, payload = invoke(
            work, document=_with_all_oracles(qual), allow_execute=True
        )
    seen = rules_in(payload)
    report(
        code != 0 and "ORACLE_EXECUTED_WITHOUT_COMMANDS" in seen,
        "EXECUTED declared with no commands is rejected as a masquerade",
        f"exit={code} rules={sorted(seen)}",
    )

    # The v1.0.0 spelling keeps working, so existing documents do not break.
    with tempfile.TemporaryDirectory() as work:
        doc = _with_all_oracles(
            {
                "mode": "executable",
                "positive_command": GOOD_ORACLE_CMD,
                "negative_command": GOOD_NEGATIVE_CMD,
            }
        )
        code, stdout, stderr, payload = invoke(work, document=doc, allow_execute=True)
    claim_payload = (payload.get("claims") or [{}])[0]
    report(
        code == 0
        and gate_of(payload) == "PASS"
        and claim_payload.get("oracle_assurance") == "EXECUTED",
        "the v1.0.0 spelling 'executable' is still accepted and means EXECUTED",
        f"exit={code} gate={gate_of(payload)} "
        f"assurance={claim_payload.get('oracle_assurance')}",
    )

    with tempfile.TemporaryDirectory() as work:
        code, stdout, stderr, payload = invoke(
            work, document=_with_all_oracles(_declared_qualification(mode="vibes"))
        )
    seen = rules_in(payload)
    report(
        code != 0 and "ORACLE_QUALIFICATION_MODE_INVALID" in seen,
        "an unrecognised qualification mode is rejected rather than guessed at",
        f"exit={code} rules={sorted(seen)}",
    )

    # Residual uncertainty is still required: the auto-recorded qualification gap
    # must not satisfy the requirement on the project's behalf.
    with tempfile.TemporaryDirectory() as work:
        doc = _with_all_oracles(_declared_qualification())
        del doc["claims"][0]["residual_uncertainty"]
        code, stdout, stderr, payload = invoke(work, document=doc)
    seen = rules_in(payload)
    report(
        code != 0 and "RESIDUAL_UNCERTAINTY_MISSING" in seen,
        "DECLARED does not excuse the claim from recording its own residual uncertainty",
        f"exit={code} rules={sorted(seen)}",
    )


def test_examples() -> None:
    """Every shipped example must pass the gate it documents.

    Documentation that describes a system it does not satisfy is a worked example
    of the problem this repository exists to solve.
    """
    print("\nD. shipped examples validate")
    examples_dir = os.path.join(REPO, "examples")
    found = 0
    for name in sorted(os.listdir(examples_dir)):
        path = os.path.join(examples_dir, name, "verification.yaml")
        if not os.path.isfile(path):
            continue
        found += 1
        proc = subprocess.run(
            [sys.executable, CDV, "validate", "--json", path],
            capture_output=True, text=True, cwd=os.path.dirname(path), timeout=120,
        )
        try:
            payload = json.loads(proc.stdout)
        except json.JSONDecodeError:
            payload = {}
        blocking = [
            f"{f.get('rule')}: {f.get('message')}"
            for f in (payload.get("document_findings") or [])
            if f.get("severity") == "ERROR"
        ]
        for claim in payload.get("claims") or []:
            for finding in claim.get("findings") or []:
                if finding.get("severity") == "ERROR":
                    blocking.append(f"{finding.get('rule')}: {finding.get('message')}")
        report(
            proc.returncode == 0 and gate_of(payload) == "PASS",
            f"examples/{name} passes the gate",
            f"exit={proc.returncode} gate={gate_of(payload)} blocking={blocking[:4]}",
        )
    if found == 0:
        report(False, "examples directory contains at least one example")


def main() -> int:
    print("=" * 78)
    print("EDGE CASE AND POSITIVE-PATH SUITE")
    print("=" * 78)

    if not os.path.isfile(CDV):
        print(f"{RED}FAIL{RESET} cannot find cdv at {CDV}")
        return 1

    test_input_handling()
    test_positive_paths()
    test_executable_oracles()
    test_qualification_assurance()
    test_examples()

    print("-" * 78)

    def verdict(token: str) -> str:
        matching = [(label, label not in failures) for label in case_labels if token in label]
        if not matching:
            return "NOT_RUN"
        return "PASS" if all(ok for _, ok in matching) else "FAIL"

    print(
        "ORACLE_EXECUTED_MODE="
        + ("CERTIFIED" if verdict("certified by executing") == "PASS" else "NOT_CERTIFIED")
    )
    print(f"ORACLE_ALWAYS_PASS_CANARY={'REJECTED' if verdict('never rejects a known-bad') == 'PASS' else 'NOT_REJECTED'}")
    print(f"ORACLE_CRASHING_CANARY={'REJECTED' if verdict('crashes rather than reporting') == 'PASS' else 'NOT_REJECTED'}")
    print(f"ORACLE_DECLARED_MODE={'VISIBLY_WEAKER' if verdict('assurance is recorded as DECLARED') == 'PASS' else 'NOT_VISIBLE'}")
    print(f"ORACLE_DECLARED_INCOMPLETE_CANARY={'REJECTED' if verdict('why executing is unavailable is rejected') == 'PASS' else 'NOT_REJECTED'}")
    print(f"ORACLE_MASQUERADE_CANARY={'REJECTED' if verdict('masquerade') == 'PASS' else 'NOT_REJECTED'}")

    if failures:
        print(f"{RED}EDGE_CASES=FAIL{RESET} ({len(failures)} case(s) failed)")
        for label in failures:
            print(f"  - {label}")
        return 1
    print(f"{GREEN}EDGE_CASES=PASS{RESET}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
