#!/usr/bin/env python3
"""Canary runner.

Executes the engine against the baseline and each mutated canary, in a separate
process per canary, and asserts the expected verdict and the expected rule.

Why a separate process per canary:
  * it exercises the same interface an adapter would use, so the test cannot pass
    by calling a private function that adapters never call
  * it makes cross-canary state leakage impossible
  * it means a crash in one canary is reported as a crash, not a wrong verdict

Exit code 0 only if every canary behaved exactly as designed.
"""

from __future__ import annotations

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
    print("FAIL: PyYAML is required to write canary documents", file=sys.stderr)
    sys.exit(3)


GREEN = "\033[32m"
RED = "\033[31m"
YELLOW = "\033[33m"
DIM = "\033[2m"
RESET = "\033[0m"


def run_cdv(document: dict, workdir: str) -> tuple[int, dict, str]:
    """Write the document, run the CLI, return (exit_code, findings, raw)."""
    path = os.path.join(workdir, "verification.yaml")
    with open(path, "w", encoding="utf-8") as handle:
        yaml.safe_dump(document, handle, sort_keys=False)

    proc = subprocess.run(
        [sys.executable, CDV, "validate", "--json", path],
        capture_output=True,
        text=True,
        cwd=workdir,
        timeout=180,
    )
    raw = proc.stdout + proc.stderr
    payload: dict = {}
    if proc.stdout.strip().startswith("{"):
        try:
            payload = json.loads(proc.stdout)
        except json.JSONDecodeError:
            payload = {}
    return proc.returncode, payload, raw


def collect_findings(payload: dict) -> list[tuple[str, str, str]]:
    """Flatten findings to (severity, rule, message)."""
    out: list[tuple[str, str, str]] = []
    for finding in payload.get("document_findings", []) or []:
        out.append((finding.get("severity", ""), finding.get("rule", ""), finding.get("message", "")))
    for claim in payload.get("claims", []) or []:
        for finding in claim.get("findings", []) or []:
            out.append((finding.get("severity", ""), finding.get("rule", ""), finding.get("message", "")))
    return out


def main() -> int:
    if not os.path.isfile(CDV):
        print(f"{RED}FAIL{RESET} cannot find cdv at {CDV}")
        return 1

    passed = 0
    failed = 0
    failures: list[str] = []

    print("=" * 78)
    print("CLAIM-DRIVEN VERIFICATION -- CANARY SUITE")
    print("=" * 78)
    print(f"{'canary':38s} {'gate':6s} {'rule asserted':40s} result")
    print("-" * 78)

    for name, mutation, expect_gate, expect_rule, expect_severity in canaries.CANARIES:
        document = canaries.build(name, mutation)
        with tempfile.TemporaryDirectory(prefix="cdv-canary-") as workdir:
            code, payload, raw = run_cdv(document, workdir)

        gate = payload.get("gate", f"<no-json exit={code}>")
        findings = collect_findings(payload)

        problems: list[str] = []
        if gate != expect_gate:
            problems.append(f"gate was {gate!r}, expected {expect_gate!r}")
        if payload.get("gate") != expect_gate:
            pass  # covered above
        # Exit code must agree with the gate, and never be 0 on a FAIL.
        if expect_gate == "PASS" and code != 0:
            problems.append(f"exit code {code} for a PASS gate")
        if expect_gate == "FAIL" and code == 0:
            problems.append("exit code 0 for a FAIL gate")

        if expect_rule:
            matched = [
                (sev, rule)
                for sev, rule, _ in findings
                if rule == expect_rule and sev == expect_severity
            ]
            if not matched:
                seen = sorted({rule for _, rule, _ in findings})
                problems.append(
                    f"rule {expect_rule} ({expect_severity}) not present; saw {seen}"
                )
        else:
            # Positive canary must carry no ERROR at all.
            errors = [(rule, msg) for sev, rule, msg in findings if sev == "ERROR"]
            if errors:
                problems.append(f"unexpected ERROR findings: {errors}")

        if problems:
            failed += 1
            print(f"{name:38s} {gate:6s} {(expect_rule or '-'):40s} {RED}FAIL{RESET}")
            for problem in problems:
                print(f"    {RED}->{RESET} {problem}")
            failures.append(name)
        else:
            passed += 1
            detail = expect_rule or "no ERROR findings"
            print(f"{name:38s} {gate:6s} {(expect_rule or '-'):40s} {GREEN}ok{RESET} {DIM}{detail}{RESET}")

    print("-" * 78)
    print(f"canaries: {passed} passed, {failed} failed")
    if failures:
        print(f"{RED}FAILED CANARIES: {', '.join(failures)}{RESET}")
        print("CDV_CANARY_SUITE=FAIL")
        return 1
    print(f"{GREEN}CDV_CANARY_SUITE=PASS{RESET}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
