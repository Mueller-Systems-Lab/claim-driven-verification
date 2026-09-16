#!/usr/bin/env python3
"""Negative and positive canaries for verification context integrity.

These are the canaries for the *observer*, not for the system under test. They
exist because of a real false PASS:

    A canary launched an agent with cwd=target while PWD still pointed at the
    caller's repository. The runtime resolved the session's project from PWD, so
    the agent executed elsewhere. "The marker file was not created" and "the
    commit count did not change" were both vacuously true, and five checks
    reported success against a system that had never been tested.

The invariant under test:

    A verification result MUST NOT be interpreted until the verifier proves it is
    observing the intended target, execution context and enforcement path.

Every case below is either the reproduction of that defect or a neighbouring way
of observing the wrong thing. Each negative case must produce FAIL, and must fail
naming the indicator that was violated -- a FAIL for the wrong reason would mean
the check is passing by accident and would be silently fragile.

Each case runs `cdv context-proof` in a subprocess with a deliberately
constructed cwd/environment, which is the only way to test inherited-state
handling honestly: simulating it in-process would test the simulation.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import uuid

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
CDV = os.path.join(REPO, "bin", "cdv")

GREEN = "\033[32m"
RED = "\033[31m"
RESET = "\033[0m"

failures: list[str] = []
results: list[tuple[str, bool]] = []


# ---------------------------------------------------------------------------
# Fixture
# ---------------------------------------------------------------------------


class Target:
    """A throwaway project shaped like a bootstrapped target."""

    def __init__(self, root: str, name: str, *, commit: bool = True) -> None:
        self.path = os.path.join(root, name)
        os.makedirs(os.path.join(self.path, ".verification", "bin"), exist_ok=True)
        os.makedirs(os.path.join(self.path, ".opencode", "plugin"), exist_ok=True)
        # Presence only: the indicator checks reachability, not execution.
        launcher = os.path.join(self.path, ".verification", "bin", "cdv")
        with open(launcher, "w", encoding="utf-8") as handle:
            handle.write("#!/bin/sh\nexit 0\n")
        os.chmod(launcher, 0o755)
        with open(
            os.path.join(self.path, ".opencode", "plugin", "verification-guard.ts"),
            "w",
            encoding="utf-8",
        ) as handle:
            handle.write("// fixture guard\n")
        self.commit = None
        if commit:
            _run(["git", "init", "-q"], self.path)
            with open(os.path.join(self.path, "README.md"), "w", encoding="utf-8") as handle:
                handle.write("fixture\n")
            _run(["git", "add", "-A"], self.path)
            _run(
                ["git", "-c", "user.email=f@f", "-c", "user.name=f", "commit", "-qm", "init"],
                self.path,
            )
            self.commit = _out(["git", "rev-parse", "HEAD"], self.path)

    def write_marker(self, nonce: str) -> None:
        path = os.path.join(self.path, ".verification", "context-marker")
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(nonce + "\n")

    def write_audit(self, events: int = 2) -> None:
        path = os.path.join(self.path, ".verification", "audit.log")
        with open(path, "w", encoding="utf-8") as handle:
            for _ in range(events):
                handle.write('{"event":"session:session.idle","gate":"FAIL"}\n')

    def remove(self, relative: str) -> None:
        target = os.path.join(self.path, relative)
        if os.path.isdir(target):
            shutil.rmtree(target, ignore_errors=True)
        elif os.path.isfile(target):
            os.remove(target)


def _run(cmd: list[str], cwd: str) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=60)


def _out(cmd: list[str], cwd: str) -> str | None:
    proc = _run(cmd, cwd)
    return proc.stdout.strip() or None if proc.returncode == 0 else None


# ---------------------------------------------------------------------------
# Invocation
# ---------------------------------------------------------------------------


def invoke(
    *,
    cwd: str,
    env_pwd: str | None,
    project: str,
    expect_realpath: str | None = None,
    expect_commit: str | None = None,
    marker: str | None = None,
    observed_project: str | None = None,
    require_guard_hooks: bool = False,
    require_pwd: bool = True,
    allow_inherited_only: bool = False,
) -> tuple[int, str]:
    """Run context-proof as a subprocess with an explicitly controlled environment."""
    cmd = [sys.executable, CDV, "context-proof", "--project", project]
    if expect_realpath:
        cmd += ["--expect-realpath", expect_realpath]
    if expect_commit:
        cmd += ["--expect-commit", expect_commit]
    if marker:
        cmd += ["--require-marker", marker]
    if observed_project:
        cmd += ["--observed-project", observed_project]
    if require_guard_hooks:
        cmd += ["--require-guard-hooks"]
    if require_pwd:
        cmd += ["--require-pwd"]
    if allow_inherited_only:
        cmd += ["--allow-inherited-only"]

    env = dict(os.environ)
    if env_pwd is None:
        env.pop("PWD", None)
    else:
        env["PWD"] = env_pwd

    proc = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=120, env=env)
    return proc.returncode, proc.stdout + proc.stderr


def check(
    label: str,
    *,
    expect_status: str,
    expect_reason_token: str | None,
    code: int,
    output: str,
) -> None:
    problems: list[str] = []
    observed_status = "PASS" if "CDV_CONTEXT_PROOF=PASS" in output else "FAIL"
    if observed_status != expect_status:
        problems.append(f"verdict was {observed_status}, expected {expect_status}")
    if expect_status == "PASS" and code != 0:
        problems.append(f"exit code {code} for a PASS verdict")
    if expect_status == "FAIL" and code == 0:
        problems.append("exit code 0 for a FAIL verdict")
    if expect_reason_token and expect_reason_token not in output:
        problems.append(
            f"no finding naming {expect_reason_token!r}; a FAIL for an unrelated "
            "reason would mean this canary passes by accident"
        )

    if problems:
        failures.append(label)
        results.append((label, False))
        print(f"  {RED}FAIL{RESET} {label}")
        for problem in problems:
            print(f"         {RED}->{RESET} {problem}")
        for line in output.strip().splitlines()[:8]:
            print(f"         {line}")
    else:
        results.append((label, True))
        tag = "FAIL_AS_DESIGNED" if expect_status == "FAIL" else "PASS"
        print(
            f"  {GREEN}ok{RESET}   {label} "
            f"{'\033[2m'}{tag} via {expect_reason_token or 'all indicators agree'}{RESET}"
        )


# ---------------------------------------------------------------------------
# Cases
# ---------------------------------------------------------------------------


def main() -> int:
    print("=" * 78)
    print("VERIFICATION CONTEXT INTEGRITY -- CANARY SUITE")
    print("=" * 78)
    print(f"{'case':48s} {'verdict':16s} reason")
    print("-" * 78)

    nonce = "nonce-" + uuid.uuid4().hex

    with tempfile.TemporaryDirectory(prefix="cdv-context-") as root:
        target = Target(root, "target")
        other = Target(root, "other-repo")
        target.write_marker(nonce)
        target.write_audit()

        marker_file = os.path.join(target.path, ".verification", "context-marker")
        audit_file = os.path.join(target.path, ".verification", "audit.log")

        # ------------------------------------------------------------------
        # POSITIVE: correct context is still accepted.
        # Without this, a check that always failed would satisfy every negative
        # case below and be worthless.
        # ------------------------------------------------------------------
        code, out = invoke(
            cwd=target.path,
            env_pwd=target.path,
            project=target.path,
            expect_commit=target.commit,
            marker=nonce,
            observed_project=target.path,
            require_guard_hooks=True,
        )
        check(
            "positive: correct target, cwd, PWD, marker, hooks",
            expect_status="PASS",
            expect_reason_token=None,
            code=code,
            output=out,
        )

        # ------------------------------------------------------------------
        # REGRESSION: the v1.0.0 false PASS, reproduced permanently.
        # cwd is the target; PWD still points at the caller's repository.
        # ------------------------------------------------------------------
        code, out = invoke(
            cwd=target.path,
            env_pwd=other.path,
            project=target.path,
            expect_commit=target.commit,
            marker=nonce,
            observed_project=target.path,
        )
        check(
            "REGRESSION wrong PWD (cwd correct, PWD stale)",
            expect_status="FAIL",
            expect_reason_token="pwd_env",
            code=code,
            output=out,
        )

        # ------------------------------------------------------------------
        # PWD agrees but the actual repository is a different one. This is the
        # inverse of the regression: the inherited indicator is right and the
        # real observation is wrong, so a check trusting PWD alone would pass.
        # ------------------------------------------------------------------
        code, out = invoke(
            cwd=other.path,
            env_pwd=target.path,
            project=target.path,
            expect_realpath=target.path,
            marker=nonce,
            observed_project=target.path,
        )
        check(
            "PWD correct but the process is in a different repository",
            expect_status="FAIL",
            expect_reason_token="cwd",
            code=code,
            output=out,
        )

        # ------------------------------------------------------------------
        # Wrong target repository outright.
        # ------------------------------------------------------------------
        code, out = invoke(
            cwd=target.path,
            env_pwd=target.path,
            project=target.path,
            expect_commit=target.commit,
            marker=nonce,
            observed_project=other.path,
            require_guard_hooks=True,
        )
        check(
            "runtime reported a different project than the target",
            expect_status="FAIL",
            expect_reason_token="observed_project",
            code=code,
            output=out,
        )

        # ------------------------------------------------------------------
        # Stale sentinel: a marker left behind by an earlier run.
        # ------------------------------------------------------------------
        code, out = invoke(
            cwd=target.path,
            env_pwd=target.path,
            project=target.path,
            expect_commit=target.commit,
            marker="nonce-from-an-earlier-run",
            observed_project=target.path,
        )
        check(
            "stale repository marker (nonce from a previous run)",
            expect_status="FAIL",
            expect_reason_token="marker_nonce",
            code=code,
            output=out,
        )

        # ------------------------------------------------------------------
        # Enforcement path never executed.
        # ------------------------------------------------------------------
        os.rename(audit_file, audit_file + ".hidden")
        try:
            code, out = invoke(
                cwd=target.path,
                env_pwd=target.path,
                project=target.path,
                expect_commit=target.commit,
                marker=nonce,
                observed_project=target.path,
                require_guard_hooks=True,
            )
            check(
                "guard hooks never fired (enforcement path inactive)",
                expect_status="FAIL",
                expect_reason_token="guard_audit_hooks",
                code=code,
                output=out,
            )
        finally:
            os.rename(audit_file + ".hidden", audit_file)

        # ------------------------------------------------------------------
        # Process outside the expected project root.
        # ------------------------------------------------------------------
        code, out = invoke(
            cwd=root,
            env_pwd=target.path,
            project=target.path,
            expect_commit=target.commit,
            marker=nonce,
            observed_project=target.path,
        )
        check(
            "process executes outside the expected project root",
            expect_status="FAIL",
            expect_reason_token="cwd",
            code=code,
            output=out,
        )

        # ------------------------------------------------------------------
        # Target path through a symlink whose identity differs.
        # ------------------------------------------------------------------
        trap = os.path.join(root, "target-link")
        os.symlink(other.path, trap)
        code, out = invoke(
            cwd=target.path,
            env_pwd=target.path,
            project=trap,
            expect_realpath=target.path,
            marker=nonce,
            observed_project=target.path,
        )
        check(
            "target path is a symlink resolving to another repository",
            expect_status="FAIL",
            expect_reason_token="project_realpath",
            code=code,
            output=out,
        )

        # A symlink pointing at the real target must still be accepted: the
        # check is on resolved identity, not on the literal path spelling.
        good_link = os.path.join(root, "target-link-ok")
        os.symlink(target.path, good_link)
        code, out = invoke(
            cwd=target.path,
            env_pwd=target.path,
            project=good_link,
            expect_realpath=target.path,
            expect_commit=target.commit,
            marker=nonce,
            observed_project=target.path,
            require_guard_hooks=True,
        )
        check(
            "symlink resolving to the target is accepted (identity, not spelling)",
            expect_status="PASS",
            expect_reason_token=None,
            code=code,
            output=out,
        )

        # ------------------------------------------------------------------
        # A proof built entirely from inherited state must be refused. The audit
        # log is moved aside so the guard's hooks are not available as
        # independent evidence, leaving only cwd and PWD.
        # ------------------------------------------------------------------
        os.rename(audit_file, audit_file + ".hidden")
        try:
            code, out = invoke(
                cwd=target.path,
                env_pwd=target.path,
                project=target.path,
                require_pwd=True,
            )
            check(
                "inherited-only indicators (cwd + PWD) cannot prove context",
                expect_status="FAIL",
                expect_reason_token="independent indicator",
                code=code,
                output=out,
            )

            # ...and the explicit opt-out is available but never silent: it has
            # to be asked for by name.
            code, out = invoke(
                cwd=target.path,
                env_pwd=target.path,
                project=target.path,
                require_pwd=True,
                allow_inherited_only=True,
            )
            check(
                "inherited-only proof is permitted only when explicitly accepted",
                expect_status="PASS",
                expect_reason_token=None,
                code=code,
                output=out,
            )
        finally:
            os.rename(audit_file + ".hidden", audit_file)

        # ------------------------------------------------------------------
        # Wrong enforcement path: the guard exists but not where the runtime
        # looks for it. OpenCode discovers {plugin,plugins}/*.{ts,js} and ignores
        # nested files, so a guard below that level never loads -- silently. A
        # guard that exists but cannot run is indistinguishable from a passing
        # project unless the position is checked.
        # ------------------------------------------------------------------
        guard_src = os.path.join(target.path, ".opencode", "plugin", "verification-guard.ts")
        guard_nested_dir = os.path.join(target.path, ".opencode", "plugin", "verification-guard")
        os.makedirs(guard_nested_dir, exist_ok=True)
        os.rename(guard_src, os.path.join(guard_nested_dir, "index.ts"))
        try:
            code, out = invoke(
                cwd=target.path,
                env_pwd=target.path,
                project=target.path,
                expect_commit=target.commit,
                marker=nonce,
                observed_project=target.path,
            )
            check(
                "guard present but not at a discoverable path",
                expect_status="FAIL",
                expect_reason_token="guard_present",
                code=code,
                output=out,
            )
        finally:
            os.rename(os.path.join(guard_nested_dir, "index.ts"), guard_src)
            os.rmdir(guard_nested_dir)

        # ------------------------------------------------------------------
        # Enforcement path absent entirely.
        # ------------------------------------------------------------------
        os.rename(guard_src, guard_src + ".hidden")
        try:
            code, out = invoke(
                cwd=target.path,
                env_pwd=target.path,
                project=target.path,
                expect_commit=target.commit,
                marker=nonce,
                observed_project=target.path,
            )
            check(
                "guard not installed at all",
                expect_status="FAIL",
                expect_reason_token="guard_present",
                code=code,
                output=out,
            )
        finally:
            os.rename(guard_src + ".hidden", guard_src)

        # ------------------------------------------------------------------
        # Missing engine: the target is not a bootstrapped project at all.
        # ------------------------------------------------------------------
        target.remove(os.path.join(".verification", "bin", "cdv"))
        try:
            code, out = invoke(
                cwd=target.path,
                env_pwd=target.path,
                project=target.path,
                expect_commit=target.commit,
                marker=nonce,
                observed_project=target.path,
            )
            check(
                "target has no installed engine",
                expect_status="FAIL",
                expect_reason_token="engine_present",
                code=code,
                output=out,
            )
        finally:
            launcher = os.path.join(target.path, ".verification", "bin", "cdv")
            with open(launcher, "w", encoding="utf-8") as handle:
                handle.write("#!/bin/sh\nexit 0\n")
            os.chmod(launcher, 0o755)

        # ------------------------------------------------------------------
        # Unset PWD when PWD was required: refuse rather than assume.
        # ------------------------------------------------------------------
        code, out = invoke(
            cwd=target.path,
            env_pwd=None,
            project=target.path,
            expect_commit=target.commit,
            marker=nonce,
            observed_project=target.path,
            require_pwd=True,
        )
        check(
            "PWD unset while required is refused, not assumed",
            expect_status="FAIL",
            expect_reason_token="pwd_env",
            code=code,
            output=out,
        )

    print("-" * 78)

    # Named verdicts for the bootstrap report. Each is derived from the case that
    # exercises it, so the report cannot claim a property no case covered.
    def verdict(token: str) -> str:
        matching = [(label, ok) for label, ok in results if token in label]
        if not matching:
            return "NOT_RUN"
        return "PASS" if all(ok for _, ok in matching) else "FAIL"

    wrong_pwd = verdict("REGRESSION wrong PWD")
    wrong_repo = verdict("different repository") if verdict("different repository") != "NOT_RUN" else verdict("resolved repository")
    print(f"CONTEXT_CANARIES={'FAIL' if failures else 'PASS'}")
    print(f"CONTEXT_POSITIVE_CANARY={verdict('positive: correct target')}")
    print(f"WRONG_PWD_REGRESSION={'REJECTED_AS_DESIGNED' if wrong_pwd == 'PASS' else 'NOT_REJECTED'}")
    print(
        "WRONG_REPOSITORY_CANARY="
        + ("REJECTED_AS_DESIGNED" if wrong_repo == "PASS" else "NOT_REJECTED")
    )
    print(
        "STALE_CONTEXT_CANARY="
        + ("REJECTED_AS_DESIGNED" if verdict("stale repository marker") == "PASS" else "NOT_REJECTED")
    )
    print(
        "GUARD_INACTIVE_CANARY="
        + ("REJECTED_AS_DESIGNED" if verdict("guard hooks never fired") == "PASS" else "NOT_REJECTED")
    )
    print(
        "ENFORCEMENT_PATH_CANARY="
        + (
            "REJECTED_AS_DESIGNED"
            if verdict("not at a discoverable path") == "PASS"
            else "NOT_REJECTED"
        )
    )

    if failures:
        print(f"{RED}CONTEXT_CANARIES=FAIL{RESET} ({len(failures)} case(s) failed)")
        for label in failures:
            print(f"  - {label}")
        return 1
    print(f"{GREEN}CONTEXT_CANARIES=PASS{RESET}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
