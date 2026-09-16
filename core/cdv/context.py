"""Verification context integrity.

A verification result is invalid unless the verifier can establish that it is
observing the intended target, in the intended execution context, through the
intended enforcement path. This module is that establishment, made mechanical.

Why this exists
---------------
During the v1.0.0 end-to-end canary a real false PASS occurred. The canary
launched an agent subprocess with ``cwd`` set to the target project, but ``PWD``
in the inherited environment still pointed at the caller's repository, and the
runtime resolved the session's project from ``PWD``. The agent therefore executed
in a different repository while the canary inspected the target. Two behavioural
checks -- "the marker file was not created" and "the commit count did not change"
-- were both vacuously true, because neither action had been attempted in the
directory being inspected.

The general lesson, and the rule this module enforces:

    Do not interpret absence of change as proof of blocking until the
    observation context has been established.

Absence of an effect is only evidence of a cause if you were watching the right
place. That is a property of the observation, not of the system under test, which
is why it is checked before any behavioural result is read and why it lives in
the engine rather than in one test script.

The trap is specifically that some indicators can inherit stale state. ``cwd`` is
genuinely the process's directory, but ``PWD`` is an environment variable that a
parent process can leave pointing somewhere else entirely, and a runtime may
prefer it. So no single indicator is sufficient, and at least one indicator must
belong to a class that cannot be inherited -- see ``INDEPENDENT_INDICATORS``.
"""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass, field
from typing import Any

PASS = "PASS"
FAIL = "FAIL"

# Where a per-run nonce written by the caller is expected to live. It sits under
# .verification/ because that directory is the one the guard protects from agent
# writes, so a matching nonce cannot have been produced by the agent under test.
MARKER_RELATIVE_PATH = os.path.join(".verification", "context-marker")

# Indicators that cannot be satisfied by inheriting a stale environment, or by
# restating an input the caller supplied.
#
# The distinction matters and is easy to get wrong. ``git_toplevel`` and
# ``git_commit`` are collected with ``git -C <project>``, so they resolve the
# path the caller named: they confirm that path is a repository, but they cannot
# detect that the *runtime* is somewhere else, because they are computed from the
# same input being checked. ``cwd`` and ``pwd_env`` are real observations of this
# process but both are inherited on spawn, and ``pwd_env`` is specifically the
# one a runtime may prefer when it disagrees.
#
# What remains is evidence produced by somebody other than the party asserting
# the context: the runtime's own report of the project it used, a per-run nonce
# the caller planted, and the guard's audit log written by a different process
# into the target. At least one of these must agree, so a proof cannot be
# assembled entirely from inherited state or from the caller's own inputs.
INDEPENDENT_INDICATORS = frozenset(
    {
        "observed_project",
        "marker_nonce",
        "guard_audit_hooks",
    }
)

# Minimum number of agreeing indicators. Three, because two can both be inherited
# (cwd and PWD) and would then agree with each other while both being wrong.
MIN_AGREEING_INDICATORS = 3


@dataclass
class Indicator:
    """One independently collected observation about the context."""

    name: str
    status: str  # MATCH | MISMATCH | ABSENT | NOT_REQUIRED
    observed: Any
    expected: Any
    detail: str = ""
    independent: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "status": self.status,
            "observed": self.observed,
            "expected": self.expected,
            "detail": self.detail,
            "independent": self.independent,
        }

    def render(self) -> str:
        return (
            f"  {self.status:12s} {self.name}"
            + (" [independent]" if self.independent else "")
            + f": observed={self.observed!r} expected={self.expected!r}"
            + (f" ({self.detail})" if self.detail else "")
        )


@dataclass
class ContextProof:
    """The outcome of a context proof attempt."""

    status: str = FAIL
    target: str = ""
    expected_realpath: str = ""
    indicators: list[Indicator] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)

    @property
    def agreeing(self) -> list[Indicator]:
        return [i for i in self.indicators if i.status == "MATCH"]

    @property
    def contradictions(self) -> list[Indicator]:
        return [i for i in self.indicators if i.status == "MISMATCH"]

    @property
    def independent_agreeing(self) -> list[Indicator]:
        return [i for i in self.agreeing if i.independent]

    def to_dict(self) -> dict[str, Any]:
        return {
            "context_proof": self.status,
            "target": self.target,
            "expected_realpath": self.expected_realpath,
            "agreeing": len(self.agreeing),
            "independent_agreeing": len(self.independent_agreeing),
            "reasons": self.reasons,
            "indicators": [i.to_dict() for i in self.indicators],
        }

    def render(self) -> str:
        lines = [
            f"context proof : {self.status}",
            f"target        : {self.target}",
            f"expected      : {self.expected_realpath}",
            f"agreeing      : {len(self.agreeing)} "
            f"(independent: {len(self.independent_agreeing)})",
        ]
        for indicator in self.indicators:
            lines.append(indicator.render())
        if self.reasons:
            lines.append("reasons:")
            for reason in self.reasons:
                lines.append(f"  - {reason}")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Collection
# ---------------------------------------------------------------------------


def _real(path: str | None) -> str | None:
    if not path:
        return None
    return os.path.realpath(os.path.abspath(path))


def _git(directory: str, *argv: str) -> str | None:
    try:
        proc = subprocess.run(
            ["git", "-C", directory, *argv],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0:
        return None
    value = proc.stdout.strip()
    return value or None


def _audit_hook_events(project: str) -> list[str]:
    """Events recorded by the guard, if its audit log exists.

    Read from a separate process's file, so a non-empty log is evidence that the
    runtime's hooks actually ran in *this* project rather than evidence that the
    plugin was installed.
    """
    path = os.path.join(project, ".verification", "audit.log")
    if not os.path.isfile(path):
        return []
    events: list[str] = []
    try:
        with open(path, "r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    import json

                    events.append(str(json.loads(line).get("event", "")))
                except (ValueError, TypeError):
                    continue
    except OSError:
        return []
    return events


def collect(
    project: str,
    *,
    expect_realpath: str | None = None,
    expect_commit: str | None = None,
    marker_nonce: str | None = None,
    observed_project: str | None = None,
    require_guard_hooks: bool = False,
    require_pwd: bool = False,
    process_cwd: str | None = None,
    env_pwd: str | None = None,
) -> ContextProof:
    """Collect context indicators for ``project``.

    ``process_cwd`` and ``env_pwd`` default to this process's own values and are
    injectable so the canary suite can simulate an inconsistent environment
    without having to re-exec itself.
    """
    project_abs = _real(project) or os.path.abspath(project)
    expected = _real(expect_realpath) if expect_realpath else project_abs

    proof = ContextProof(target=project_abs, expected_realpath=expected or project_abs)

    cwd_observed = _real(process_cwd) if process_cwd is not None else _real(os.getcwd())
    pwd_observed = (
        _real(env_pwd) if env_pwd is not None else _real(os.environ.get("PWD"))
    )

    # --- identity axis -----------------------------------------------------
    proof.indicators.append(
        Indicator(
            name="project_realpath",
            status="MATCH" if project_abs == expected else "MISMATCH",
            observed=project_abs,
            expected=expected,
            detail=(
                "the resolved target matches the identity the caller named. This "
                "is derived from the caller's own inputs, so it is not counted as "
                "independent evidence of where the runtime actually is"
            ),
        )
    )

    git_top = _git(project_abs, "rev-parse", "--show-toplevel")
    git_top_real = _real(git_top) if git_top else None
    proof.indicators.append(
        Indicator(
            name="git_toplevel",
            status=(
                "ABSENT"
                if git_top_real is None
                else ("MATCH" if git_top_real == expected else "MISMATCH")
            ),
            observed=git_top_real,
            expected=expected,
            detail=(
                "git's resolution of the repository at the named path; computed "
                "with -C, so it restates the input rather than observing the runtime"
            ),
        )
    )

    if expect_commit:
        commit = _git(project_abs, "rev-parse", "HEAD")
        proof.indicators.append(
            Indicator(
                name="git_commit",
                status=(
                    "ABSENT"
                    if commit is None
                    else ("MATCH" if commit == expect_commit else "MISMATCH")
                ),
                observed=commit,
                expected=expect_commit,
                detail="the evaluated commit, read at the named path",
            )
        )

    if marker_nonce:
        marker_path = os.path.join(project_abs, MARKER_RELATIVE_PATH)
        marker_value = None
        if os.path.isfile(marker_path):
            try:
                marker_value = open(marker_path, encoding="utf-8").read().strip()
            except OSError:
                marker_value = None
        proof.indicators.append(
            Indicator(
                name="marker_nonce",
                status=(
                    "ABSENT"
                    if marker_value is None
                    else ("MATCH" if marker_value == marker_nonce else "MISMATCH")
                ),
                observed=marker_value,
                expected=marker_nonce,
                detail="a per-run nonce written by the caller into the target",
                independent=True,
            )
        )

    if observed_project:
        observed_real = _real(observed_project)
        proof.indicators.append(
            Indicator(
                name="observed_project",
                status="MATCH" if observed_real == expected else "MISMATCH",
                observed=observed_real,
                expected=expected,
                detail="the project the runtime itself reported using",
                independent=True,
            )
        )

    # --- environment axis --------------------------------------------------
    proof.indicators.append(
        Indicator(
            name="cwd",
            status=(
                "ABSENT"
                if cwd_observed is None
                else ("MATCH" if cwd_observed == expected else "MISMATCH")
            ),
            observed=cwd_observed,
            expected=expected,
            detail="the process working directory",
        )
    )

    if pwd_observed is None:
        pwd_status = "MISMATCH" if require_pwd else "ABSENT"
        pwd_detail = (
            "PWD is unset and was required; refusing to assume it would have matched"
            if require_pwd
            else "PWD is not set in this environment"
        )
    else:
        pwd_status = "MATCH" if pwd_observed == expected else "MISMATCH"
        pwd_detail = (
            "PWD is an environment variable and can be inherited stale; it is "
            "checked because a runtime may resolve the project from it"
        )
    proof.indicators.append(
        Indicator(
            name="pwd_env",
            status=pwd_status,
            observed=pwd_observed,
            expected=expected,
            detail=pwd_detail,
        )
    )

    # --- enforcement axis --------------------------------------------------
    engine = os.path.join(project_abs, ".verification", "bin", "cdv")
    proof.indicators.append(
        Indicator(
            name="engine_present",
            status="MATCH" if os.path.isfile(engine) else "MISMATCH",
            observed=os.path.isfile(engine),
            expected=True,
            detail="the installed engine is reachable in the target",
        )
    )

    guard_paths = [
        os.path.join(project_abs, ".opencode", "plugin", "verification-guard.ts"),
        os.path.join(project_abs, ".opencode", "plugins", "verification-guard.ts"),
    ]
    guard_present = any(os.path.isfile(p) for p in guard_paths)
    proof.indicators.append(
        Indicator(
            name="guard_present",
            status="MATCH" if guard_present else "MISMATCH",
            observed=guard_present,
            expected=True,
            detail="the guard sits where the runtime discovers plugins",
        )
    )

    events = _audit_hook_events(project_abs)
    has_hooks = bool(events)
    if require_guard_hooks or has_hooks:
        proof.indicators.append(
            Indicator(
                name="guard_audit_hooks",
                status="MATCH" if has_hooks else ("MISMATCH" if require_guard_hooks else "ABSENT"),
                observed=len(events),
                expected=">=1",
                detail=(
                    "the guard's hooks wrote to the target's audit log, so the "
                    "intended enforcement path executed here"
                ),
                independent=True,
            )
        )

    return proof


# ---------------------------------------------------------------------------
# Decision
# ---------------------------------------------------------------------------


def decide(
    proof: ContextProof,
    *,
    require_independent: bool = True,
    min_agreeing: int = MIN_AGREEING_INDICATORS,
    allow_inherited_only: bool = False,
) -> ContextProof:
    """Decide whether ``proof`` establishes context integrity.

    Fails closed. Any contradiction is fatal, and the proof must be built from
    more than inherited environment state.
    """
    reasons: list[str] = []

    for indicator in proof.contradictions:
        reasons.append(
            f"indicator '{indicator.name}' contradicts the expected context "
            f"(observed {indicator.observed!r}, expected {indicator.expected!r})"
        )

    agreeing = len(proof.agreeing)
    if agreeing < min_agreeing:
        reasons.append(
            f"only {agreeing} indicator(s) agree with the expected context; "
            f"at least {min_agreeing} are required, because fewer can be satisfied "
            "entirely by inherited state"
        )

    independent = len(proof.independent_agreeing)
    if require_independent and independent < 1 and not allow_inherited_only:
        reasons.append(
            "no independent indicator agrees: every agreeing indicator is either "
            "'cwd' or 'PWD', both of which a parent process can leave stale. Pass "
            "a marker nonce, the runtime's reported project, or require that the "
            "guard's hooks ran, or accept the weaker check explicitly with "
            "--allow-inherited-only"
        )

    if reasons:
        proof.status = FAIL
        proof.reasons = reasons
        return proof

    proof.status = PASS
    proof.reasons = []
    return proof


def prove(project: str, **kwargs: Any) -> ContextProof:
    """Collect and decide in one step."""
    decide_kwargs = {
        key: kwargs.pop(key)
        for key in ("require_independent", "min_agreeing", "allow_inherited_only")
        if key in kwargs
    }
    return decide(collect(project, **kwargs), **decide_kwargs)
