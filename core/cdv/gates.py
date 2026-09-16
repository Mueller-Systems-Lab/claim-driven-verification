"""The gate engine.

Evaluates a loaded verification document and decides, for every claim, whether
it is sufficiently supported. All decisions are deterministic: the same document
and the same evaluated state always yield the same verdict, with no model
judgement anywhere in the path.

Design invariants
-----------------
1. FAIL CLOSED. Anything unrecognised, missing or ambiguous blocks PASS rather
   than being ignored. Unknown top-level keys, unknown claim types and unknown
   results are errors.
2. NO SELF-ATTESTATION. Independence, freshness and oracle qualification are
   derived from recorded facts, never accepted as an assertion by the party that
   produced the evidence.
3. CRITICALITY CANNOT BE DOWNGRADED AWAY. A claim whose text matches a
   criticality trigger is validated with the critical rule set regardless of how
   it labels itself, so a NON_CRITICAL label can never reduce the evidence bar.
4. FAILURE MODES COME FIRST. Coverage is computed failure-mode-by-failure-mode,
   so evidence that has no failure mode to detect cannot contribute.
"""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass, field
from typing import Any

from . import independence as indep_mod
from . import model
from .errors import (
    ERROR,
    INFO,
    WARNING,
    CdvError,
    Finding,
    # rule ids
    RULE_CLAIM_MISSING_ID,
    RULE_CLAIM_MISSING_STATEMENT,
    RULE_CLAIM_MISSING_TYPE,
    RULE_CLAIM_NOT_PASS,
    RULE_CLAIM_TYPE_INVALID,
    RULE_CONFLICT_RESOLUTION_INCOMPLETE,
    RULE_CRITICALITY_DOWNGRADE_BLOCKED,
    RULE_CRITICALITY_INVALID,
    RULE_CRITICALITY_MISSING,
    RULE_CRITICALITY_REVIEW_INCOMPLETE,
    RULE_CRITICALITY_TRIGGER_MATCHED,
    RULE_CRITICAL_CLAIM_WITHOUT_FAILURE_MODES,
    RULE_DUPLICATE_CLAIM_ID,
    RULE_DUPLICATE_EVIDENCE_ID,
    RULE_DUPLICATE_FAILURE_MODE_ID,
    RULE_EVIDENCE_CONFLICT_RESOLVED,
    RULE_EVIDENCE_CONFLICT_UNRESOLVED,
    RULE_EVIDENCE_FAILURE_MODE_UNKNOWN,
    RULE_EVIDENCE_MISSING_ID,
    RULE_EVIDENCE_NOT_FINAL_STATE,
    RULE_EVIDENCE_NO_FAILURE_MODE,
    RULE_EVIDENCE_ORACLE_UNDEFINED,
    RULE_EVIDENCE_RESULT_INVALID,
    RULE_EVIDENCE_RESULT_NOT_PASS,
    RULE_EVIDENCE_RESULT_SUPERSEDED,
    RULE_EVIDENCE_STATE_UNBOUND,
    RULE_FAILURE_MODE_MISSING_ID,
    RULE_FAILURE_MODE_UNCOVERED,
    RULE_FAILURE_MODE_WITHOUT_ORACLE,
    RULE_GATE_FAIL,
    RULE_INDEPENDENCE_EXCEPTION_INVALID,
    RULE_INSUFFICIENT_INDEPENDENT_PATHS,
    RULE_NONCRITICAL_MISSING_RATIONALE,
    RULE_ORACLE_KIND_INVALID,
    RULE_ORACLE_NOT_QUALIFIED,
    RULE_ORACLE_QUALIFICATION_BLOCKED,
    RULE_ORACLE_QUALIFICATION_UNVERIFIED,
    RULE_ORACLE_UNDEFINED,
    RULE_RESIDUAL_UNCERTAINTY_MISSING,
    RULE_RESIDUAL_UNCERTAINTY_NOT_JUSTIFIED,
    RULE_STALE_REUSE_NOT_JUSTIFIED,
    RULE_STATE_MISSING,
    RULE_TEMPORAL_CHECKS_MISSING,
    RULE_TEMPORAL_CHECK_INVALID,
    RULE_USER_OUTCOME_UNVERIFIED,
    RULE_WORLD_VERIFICATION_MISSING,
    RULE_WORLD_VERIFICATION_SELF_REPORTED,
    RULE_CORRELATED_EVIDENCE,
)

# --- Temporal vocabulary ---------------------------------------------------
TEMPORAL_KINDS = (
    "repeated-execution",
    "cold-start",
    "process-restart",
    "application-restart",
    "interruption",
    "retry",
    "recovery",
    "persistence-boundary",
    "state-rehydration",
)
# A one-time success must not prove a temporal claim, so a temporal claim needs
# both a repetition anchor and a durability anchor.
TEMPORAL_REPETITION_ANCHORS = ("repeated-execution", "retry", "interruption")
TEMPORAL_DURABILITY_ANCHORS = (
    "cold-start",
    "process-restart",
    "application-restart",
    "recovery",
    "persistence-boundary",
    "state-rehydration",
)

# Oracle kinds that can speak about the world rather than about the software's
# own opinion of itself.
EXTERNAL_ORACLE_KINDS = ("independent-technical", "cross-modal", "external-reality")

MIN_INDEPENDENCE_EXCEPTION_LENGTH = 40


def _mapping(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _sequence(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def _text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


@dataclass
class ClaimResult:
    """Outcome for a single completion claim."""

    claim_id: str
    statement: str
    claim_type: str
    declared_criticality: str
    effective_criticality: str
    status: str = "FAIL"
    downgraded: bool = False
    findings: list[Finding] = field(default_factory=list)
    independence: dict[str, Any] = field(default_factory=dict)
    coverage: dict[str, Any] = field(default_factory=dict)
    residual_uncertainty: list[str] = field(default_factory=list)
    oracles_used: list[str] = field(default_factory=list)
    evidence_count: int = 0
    passing_evidence: list[str] = field(default_factory=list)

    @property
    def blocking(self) -> list[Finding]:
        return [f for f in self.findings if f.severity == ERROR]

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.claim_id,
            "statement": self.statement,
            "type": self.claim_type,
            "declared_criticality": self.declared_criticality,
            "effective_criticality": self.effective_criticality,
            "status": self.status,
            "downgraded": self.downgraded,
            "oracles_used": self.oracles_used,
            "evidence_count": self.evidence_count,
            "passing_evidence": self.passing_evidence,
            "independence": self.independence,
            "coverage": self.coverage,
            "residual_uncertainty": self.residual_uncertainty,
            "findings": [f.to_dict() for f in self.findings],
        }


@dataclass
class ValidationResult:
    """Outcome for the whole document."""

    gate: str = "FAIL"
    claims: list[ClaimResult] = field(default_factory=list)
    findings: list[Finding] = field(default_factory=list)
    document: dict[str, Any] = field(default_factory=dict)
    path: str = ""
    counts: dict[str, int] = field(default_factory=dict)

    @property
    def exit_code(self) -> int:
        return 0 if self.gate == "PASS" else 1

    def all_findings(self) -> list[Finding]:
        out = list(self.findings)
        for claim in self.claims:
            out.extend(claim.findings)
        return out

    def to_dict(self) -> dict[str, Any]:
        return {
            "gate": self.gate,
            "document": self.path,
            "counts": self.counts,
            "document_findings": [f.to_dict() for f in self.findings],
            "claims": [c.to_dict() for c in self.claims],
        }


# ---------------------------------------------------------------------------
# Oracle qualification
# ---------------------------------------------------------------------------


def _qualify_executable(
    oracle_id: str,
    definition: dict[str, Any],
    project_dir: str,
    findings: list[Finding],
) -> None:
    """Run a project-supplied oracle against its known-good / known-bad cases.

    This is the verifier-of-the-verifier path for oracles this project controls:
    the oracle must accept a known-good input AND reject a deliberately corrupt
    one. The corrupt case is the load-bearing half; an oracle that has never been
    shown to reject a bad result is not evidence.
    """
    qual = _mapping(definition.get("qualification"))

    for label, command_key, marker, expected in (
        ("positive_case", "positive_command", "CDV_ORACLE_POSITIVE=", model.QUAL_PASS),
        ("negative_case", "negative_command", "CDV_ORACLE_NEGATIVE=", model.QUAL_DETECTED),
    ):
        command = _text(qual.get(command_key))
        if not command:
            findings.append(
                Finding(
                    rule=RULE_ORACLE_QUALIFICATION_BLOCKED,
                    severity=ERROR,
                    message=(
                        f"oracle '{oracle_id}' declares mode: executable but has no "
                        f"{command_key}; the {label} cannot be executed"
                    ),
                    subject=oracle_id,
                )
            )
            continue
        try:
            proc = subprocess.run(  # noqa: S602 - documented, opt-in, project-owned
                command,
                shell=True,
                cwd=project_dir or None,
                capture_output=True,
                text=True,
                timeout=120,
            )
        except subprocess.TimeoutExpired:
            findings.append(
                Finding(
                    rule=RULE_ORACLE_QUALIFICATION_UNVERIFIED,
                    severity=ERROR,
                    message=f"oracle '{oracle_id}' {command_key} timed out after 120s",
                    subject=oracle_id,
                )
            )
            continue

        stdout = proc.stdout or ""
        observed = None
        for line in stdout.splitlines():
            line = line.strip()
            if line.startswith(marker):
                observed = line[len(marker):].strip()
        if proc.returncode != 0 or observed != expected:
            findings.append(
                Finding(
                    rule=RULE_ORACLE_QUALIFICATION_BLOCKED,
                    severity=ERROR,
                    message=(
                        f"oracle '{oracle_id}' failed executable {label}: expected "
                        f"{marker}{expected}, got exit={proc.returncode} "
                        f"marker={observed!r}"
                    ),
                    subject=oracle_id,
                    detail={"stdout": stdout[-2000:], "stderr": (proc.stderr or "")[-2000:]},
                )
            )
        else:
            findings.append(
                Finding(
                    rule="ORACLE_QUALIFICATION_EXECUTED",
                    severity=INFO,
                    message=f"oracle '{oracle_id}' {label} executed and behaved as expected",
                    subject=oracle_id,
                )
            )


def qualify_oracle(
    oracle_id: str,
    oracles: dict[str, Any],
    project_dir: str,
    *,
    required: bool,
    allow_execute: bool,
    findings: list[Finding],
) -> dict[str, Any] | None:
    """Check that an oracle is defined and, where required, qualified."""
    if oracle_id not in oracles:
        findings.append(
            Finding(
                rule=RULE_ORACLE_UNDEFINED,
                severity=ERROR,
                message=(
                    f"oracle '{oracle_id}' is referenced but not defined in the "
                    "top-level 'oracles' mapping"
                ),
                subject=oracle_id,
            )
        )
        return None

    definition = _mapping(oracles.get(oracle_id))
    kind = _text(definition.get("kind"))
    if kind not in model.ORACLE_KINDS:
        findings.append(
            Finding(
                rule=RULE_ORACLE_KIND_INVALID,
                severity=ERROR,
                message=(
                    f"oracle '{oracle_id}' has kind {kind!r}; expected one of "
                    f"{list(model.ORACLE_KINDS)}"
                ),
                subject=oracle_id,
            )
        )

    if not required:
        return {"id": oracle_id, "kind": kind, "qualified": None}

    qual = _mapping(definition.get("qualification"))
    if not qual:
        findings.append(
            Finding(
                rule=RULE_ORACLE_NOT_QUALIFIED,
                severity=ERROR,
                message=(
                    f"oracle '{oracle_id}' carries a critical claim but has no "
                    "qualification block; a verifier that has never demonstrated "
                    "detection of a known bad result cannot be trusted for critical "
                    "evidence"
                ),
                subject=oracle_id,
            )
        )
        return {"id": oracle_id, "kind": kind, "qualified": False}

    mode = _text(qual.get("mode")) or "declared"
    if mode == "executable":
        if not allow_execute:
            findings.append(
                Finding(
                    rule=RULE_ORACLE_QUALIFICATION_UNVERIFIED,
                    severity=ERROR,
                    message=(
                        f"oracle '{oracle_id}' requires executable qualification but "
                        "oracle execution is not permitted in this run; re-run with "
                        "--allow-execute-oracles to certify it"
                    ),
                    subject=oracle_id,
                )
            )
            return {"id": oracle_id, "kind": kind, "qualified": False, "mode": mode}
        _qualify_executable(oracle_id, definition, project_dir, findings)
        return {"id": oracle_id, "kind": kind, "qualified": True, "mode": mode}

    # Declared mode: the project states that the cases were run.
    positive = _mapping(qual.get("positive_case"))
    negative = _mapping(qual.get("negative_case"))
    pos_result = _text(positive.get("result"))
    neg_result = _text(negative.get("result"))

    problems = []
    if not positive:
        problems.append("no positive_case declared")
    elif pos_result != model.QUAL_PASS:
        problems.append(
            f"positive_case result is {pos_result!r}, expected '{model.QUAL_PASS}'"
        )
    if not negative:
        problems.append("no negative_case declared")
    elif neg_result != model.QUAL_DETECTED:
        problems.append(
            f"negative_case result is {neg_result!r}, expected "
            f"'{model.QUAL_DETECTED}' (the oracle must be shown to reject a "
            "deliberately incorrect result)"
        )

    if problems:
        findings.append(
            Finding(
                rule=RULE_ORACLE_QUALIFICATION_BLOCKED,
                severity=ERROR,
                message=f"oracle '{oracle_id}' is not qualified: " + "; ".join(problems),
                subject=oracle_id,
            )
        )
        return {"id": oracle_id, "kind": kind, "qualified": False, "mode": mode}

    return {"id": oracle_id, "kind": kind, "qualified": True, "mode": mode}


# ---------------------------------------------------------------------------
# Per-claim evaluation
# ---------------------------------------------------------------------------


def _evaluate_claim(
    claim: Any,
    index: int,
    *,
    oracles: dict[str, Any],
    top_state: dict[str, Any],
    conflicts: list[Any],
    independence_exceptions: list[Any],
    criticality_exceptions: list[Any],
    project_dir: str,
    allow_execute: bool,
) -> ClaimResult:
    result = ClaimResult(
        claim_id=f"<claim#{index}>",
        statement="",
        claim_type="",
        declared_criticality="",
        effective_criticality=model.CRITICAL,
    )
    findings = result.findings

    if not isinstance(claim, dict):
        findings.append(
            Finding(
                rule=RULE_CLAIM_MISSING_STATEMENT,
                severity=ERROR,
                message=f"claim #{index} is not a mapping",
            )
        )
        result.status = "FAIL"
        return result

    # --- identity ----------------------------------------------------------
    claim_id = _text(claim.get("id"))
    if not claim_id:
        findings.append(
            Finding(
                rule=RULE_CLAIM_MISSING_ID,
                severity=ERROR,
                message=f"claim #{index} has no 'id'",
            )
        )
        claim_id = f"<claim#{index}>"
    result.claim_id = claim_id

    statement = _text(claim.get("statement"))
    if not statement:
        findings.append(
            Finding(
                rule=RULE_CLAIM_MISSING_STATEMENT,
                severity=ERROR,
                message="claim has no 'statement'",
            )
        )
    result.statement = statement

    claim_type = _text(claim.get("type")).upper()
    if not claim_type:
        findings.append(
            Finding(
                rule=RULE_CLAIM_MISSING_TYPE,
                severity=ERROR,
                message="claim has no 'type'",
            )
        )
    elif claim_type not in model.CLAIM_TYPES:
        findings.append(
            Finding(
                rule=RULE_CLAIM_TYPE_INVALID,
                severity=ERROR,
                message=(
                    f"type {claim_type!r} is not in the guidance vocabulary "
                    f"{list(model.CLAIM_TYPES)}; the vocabulary is guidance, but an "
                    "unrecognised value is rejected rather than guessed at"
                ),
            )
        )
    result.claim_type = claim_type

    # --- failure modes -----------------------------------------------------
    failure_modes = _sequence(claim.get("failure_modes"))
    fm_ids: list[str] = []
    seen_fm: set[str] = set()
    for fm_index, fm in enumerate(failure_modes):
        fm = _mapping(fm)
        fm_id = _text(fm.get("id"))
        if not fm_id:
            findings.append(
                Finding(
                    rule=RULE_FAILURE_MODE_MISSING_ID,
                    severity=ERROR,
                    message=f"failure mode #{fm_index} has no 'id'",
                )
            )
            fm_id = f"<fm#{fm_index}>"
        if fm_id in seen_fm:
            findings.append(
                Finding(
                    rule=RULE_DUPLICATE_FAILURE_MODE_ID,
                    severity=ERROR,
                    message=f"failure mode id '{fm_id}' is declared more than once",
                    subject=fm_id,
                )
            )
        seen_fm.add(fm_id)
        if not _text(fm.get("oracle")):
            findings.append(
                Finding(
                    rule=RULE_FAILURE_MODE_WITHOUT_ORACLE,
                    severity=ERROR,
                    message=(
                        "failure mode has no oracle; every critical failure mode must "
                        "have at least one explicit oracle"
                    ),
                    subject=fm_id,
                )
            )
        fm_ids.append(fm_id)

    # --- evidence ----------------------------------------------------------
    evidence_list = _sequence(claim.get("evidence"))
    evidence: list[dict[str, Any]] = []
    seen_ev: set[str] = set()
    for ev_index, ev in enumerate(evidence_list):
        ev = _mapping(ev)
        ev_id = _text(ev.get("id"))
        if not ev_id:
            findings.append(
                Finding(
                    rule=RULE_EVIDENCE_MISSING_ID,
                    severity=ERROR,
                    message=f"evidence #{ev_index} has no 'id'",
                )
            )
            ev_id = f"<ev#{ev_index}>"
        if ev_id in seen_ev:
            findings.append(
                Finding(
                    rule=RULE_DUPLICATE_EVIDENCE_ID,
                    severity=ERROR,
                    message=f"evidence id '{ev_id}' is declared more than once",
                    subject=ev_id,
                )
            )
        seen_ev.add(ev_id)
        entry = dict(ev)
        entry["id"] = ev_id
        evidence.append(entry)
    result.evidence_count = len(evidence)

    # --- declared criticality ----------------------------------------------
    declared = _text(claim.get("criticality")).upper()
    if not declared:
        findings.append(
            Finding(
                rule=RULE_CRITICALITY_MISSING,
                severity=ERROR,
                message="claim has no 'criticality'; classification must be explicit",
            )
        )
        declared = model.CRITICAL
    elif declared not in model.CRITICALITY_VALUES:
        findings.append(
            Finding(
                rule=RULE_CRITICALITY_INVALID,
                severity=ERROR,
                message=(
                    f"criticality {declared!r} is invalid; expected one of "
                    f"{list(model.CRITICALITY_VALUES)}"
                ),
            )
        )
        declared = model.CRITICAL
    result.declared_criticality = declared

    # --- criticality defaults / anti-downgrade -----------------------------
    # Criticality is a property of what the claim *asserts*, so only the claim's
    # own subject matter is scanned: its statement, its identity, and the failure
    # modes it considers. Evidence descriptions are deliberately excluded --
    # they describe mechanism ("...prints the engine version") and scanning them
    # would classify a claim by the incidental wording of how it was tested.
    trigger_texts: list[Any] = [statement, claim_id, claim_type, claim.get("notes")]
    for fm in failure_modes:
        fm = _mapping(fm)
        trigger_texts.append(fm.get("id"))
        trigger_texts.append(fm.get("description"))

    triggers = model.match_criticality_triggers(*trigger_texts)

    if declared == model.NON_CRITICAL:
        rationale = _text(claim.get("criticality_rationale"))
        if len(rationale) < model.MIN_RATIONALE_LENGTH:
            findings.append(
                Finding(
                    rule=RULE_NONCRITICAL_MISSING_RATIONALE,
                    severity=ERROR,
                    message=(
                        f"NON_CRITICAL classification requires a rationale of at "
                        f"least {model.MIN_RATIONALE_LENGTH} characters "
                        f"(found {len(rationale)})"
                    ),
                )
            )

    if triggers:
        # Effective criticality is CRITICAL whenever the claim touches a
        # critical area, regardless of its own label.
        result.effective_criticality = model.CRITICAL
        if declared == model.NON_CRITICAL:
            result.downgraded = True
            matching_exception = None
            for exc in criticality_exceptions:
                exc = _mapping(exc)
                if _text(exc.get("claim_id")) == claim_id:
                    matching_exception = exc
                    break

            if matching_exception is None:
                findings.append(
                    Finding(
                        rule=RULE_CRITICALITY_REVIEW_INCOMPLETE,
                        severity=ERROR,
                        message=(
                            "claim matches criticality trigger(s) "
                            f"{sorted(triggers)} but is classified NON_CRITICAL with "
                            "no criticality_exceptions entry; conservative default is "
                            "CRITICAL"
                        ),
                        detail={"matched_triggers": sorted(triggers)},
                    )
                )
            else:
                missing = [
                    key
                    for key in ("justification", "reviewed_by", "reviewed_at")
                    if not _text(matching_exception.get(key))
                ]
                if missing:
                    findings.append(
                        Finding(
                            rule=RULE_CRITICALITY_REVIEW_INCOMPLETE,
                            severity=ERROR,
                            message=(
                                f"criticality_exceptions entry for '{claim_id}' is "
                                f"incomplete; missing {missing}"
                            ),
                            detail={"missing": missing},
                        )
                    )
                else:
                    findings.append(
                        Finding(
                            rule=RULE_CRITICALITY_DOWNGRADE_BLOCKED,
                            severity=WARNING,
                            message=(
                                f"claim is labelled NON_CRITICAL but matches "
                                f"criticality trigger(s) {sorted(triggers)}; the "
                                "critical rule set was applied anyway, so the "
                                "reclassification could not lower the evidence bar"
                            ),
                            detail={"matched_triggers": sorted(triggers)},
                        )
                    )
        else:
            # Explain the classification. A verdict the author cannot account
            # for is a verdict they will work around.
            findings.append(
                Finding(
                    rule=RULE_CRITICALITY_TRIGGER_MATCHED,
                    severity=INFO,
                    message=(
                        f"classified CRITICAL because the claim matches trigger "
                        f"area(s) {sorted(triggers)}"
                    ),
                    detail={"matched_triggers": sorted(triggers)},
                )
            )
    else:
        result.effective_criticality = (
            model.CRITICAL if declared == model.CRITICAL else model.NON_CRITICAL
        )

    is_critical = result.effective_criticality == model.CRITICAL

    # --- failure-mode obligation -------------------------------------------
    if is_critical and not failure_modes:
        findings.append(
            Finding(
                rule=RULE_CRITICAL_CLAIM_WITHOUT_FAILURE_MODES,
                severity=ERROR,
                message=(
                    "critical claim declares no failure modes; failure-mode analysis "
                    "must precede evidence design"
                ),
            )
        )

    # --- oracles used ------------------------------------------------------
    oracle_ids: list[str] = []
    for fm in failure_modes:
        oid = _text(_mapping(fm).get("oracle"))
        if oid and oid not in oracle_ids:
            oracle_ids.append(oid)
    for ev in evidence:
        oid = _text(ev.get("oracle"))
        if oid and oid not in oracle_ids:
            oracle_ids.append(oid)
    result.oracles_used = oracle_ids

    # Qualification findings are emitted as a side effect; the result itself is
    # not carried forward, because "is this oracle qualified" is decided once and
    # never re-consulted. A local that is written and never read invites a reader
    # to believe a decision depends on it.
    for oid in oracle_ids:
        qualify_oracle(
            oid,
            oracles,
            project_dir,
            required=is_critical,
            allow_execute=allow_execute,
            findings=findings,
        )

    # --- evidence validity -------------------------------------------------
    per_evidence: dict[str, dict[str, Any]] = {}
    # Evidence whose result is not PASS is not rejected here. Whether it blocks
    # the claim depends on whether a resolved conflict explains it, and that is
    # only known after the conflict pass below. Rejecting it immediately would
    # make a root-caused conflict impossible to close, because the spec requires
    # the disagreeing evidence to remain traceable in the document.
    non_pass_evidence: list[tuple[str, str]] = []
    for ev in evidence:
        ev_id = ev["id"]
        oid = _text(ev.get("oracle"))
        if not oid:
            findings.append(
                Finding(
                    rule=RULE_EVIDENCE_ORACLE_UNDEFINED,
                    severity=ERROR,
                    message="evidence has no oracle; it cannot say what determined its result",
                    subject=ev_id,
                )
            )
        elif oid not in oracles:
            findings.append(
                Finding(
                    rule=RULE_EVIDENCE_ORACLE_UNDEFINED,
                    severity=ERROR,
                    message=f"evidence references undefined oracle '{oid}'",
                    subject=ev_id,
                )
            )

        ev_result = _text(ev.get("result")).upper()
        if ev_result not in model.RESULT_VALUES:
            findings.append(
                Finding(
                    rule=RULE_EVIDENCE_RESULT_INVALID,
                    severity=ERROR,
                    message=(
                        f"result {ev_result!r} is not one of {list(model.RESULT_VALUES)}"
                    ),
                    subject=ev_id,
                )
            )
        elif ev_result != model.RESULT_PASS:
            non_pass_evidence.append((ev_id, ev_result))

        # freshness
        fresh = evaluate_freshness_cached(top_state, ev, is_critical=is_critical)
        if fresh["status"] == "UNBOUND":
            findings.append(
                Finding(
                    rule=RULE_EVIDENCE_STATE_UNBOUND,
                    severity=ERROR,
                    message=fresh["explanation"],
                    subject=ev_id,
                )
            )
        elif fresh["status"] == "STALE":
            if fresh["reuse_accepted"]:
                findings.append(
                    Finding(
                        rule=RULE_STALE_REUSE_NOT_JUSTIFIED,
                        severity=WARNING,
                        message=fresh["explanation"],
                        subject=ev_id,
                        detail={"mismatches": fresh["mismatches"]},
                    )
                )
            else:
                findings.append(
                    Finding(
                        rule=RULE_EVIDENCE_NOT_FINAL_STATE,
                        severity=ERROR,
                        message=fresh["explanation"],
                        subject=ev_id,
                        detail={"mismatches": fresh["mismatches"]},
                    )
                )

        # failure-mode binding
        declared_fms = [_text(x) for x in _sequence(ev.get("failure_modes")) if _text(x)]
        if declared_fms:
            unknown = [x for x in declared_fms if x not in fm_ids]
            if unknown:
                findings.append(
                    Finding(
                        rule=RULE_EVIDENCE_FAILURE_MODE_UNKNOWN,
                        severity=ERROR,
                        message=f"evidence binds to unknown failure mode(s) {unknown}",
                        subject=ev_id,
                    )
                )
            covers = set(declared_fms)
        elif oid:
            covers = {
                _text(_mapping(fm).get("id"))
                for fm in failure_modes
                if _text(_mapping(fm).get("oracle")) == oid
            }
            if is_critical and not covers:
                findings.append(
                    Finding(
                        rule=RULE_EVIDENCE_NO_FAILURE_MODE,
                        severity=ERROR,
                        message=(
                            f"evidence via oracle '{oid}' maps to no failure mode; "
                            "evidence that detects no identified failure mode cannot "
                            "discharge a critical claim"
                        ),
                        subject=ev_id,
                    )
                )
        else:
            covers = set()

        per_evidence[ev_id] = {
            "entry": ev,
            "oracle": oid,
            "result": ev_result,
            "covers": covers,
            "freshness": fresh,
        }

    # --- failure-mode coverage --------------------------------------------
    # An evidence path covers a failure mode when it binds to it explicitly, or
    # when its oracle is the failure mode's own oracle. Coverage deliberately
    # does NOT require the evidence to come through the failure mode's declared
    # oracle: two independently qualified oracles may both be able to detect the
    # same failure, and the architecture wants exactly that kind of independent
    # detection. The declared oracle is still required to exist and be qualified.
    coverage: dict[str, list[str]] = {}
    for fm in failure_modes:
        fm = _mapping(fm)
        fm_id = _text(fm.get("id")) or f"<fm>"
        oid = _text(fm.get("oracle"))
        passing: list[str] = []
        for ev_id, info in per_evidence.items():
            if info["result"] != model.RESULT_PASS:
                continue
            if fm_id not in info["covers"]:
                continue
            stale = info["freshness"]["status"] == "STALE"
            if stale and info["freshness"]["reuse_accepted"] is False:
                continue
            passing.append(ev_id)
        coverage[fm_id] = sorted(passing)
        if not passing:
            findings.append(
                Finding(
                    rule=RULE_FAILURE_MODE_UNCOVERED,
                    severity=ERROR,
                    message=(
                        f"failure mode '{fm_id}' has no passing evidence via oracle "
                        f"'{oid}'"
                    ),
                    subject=fm_id,
                )
            )
    result.coverage = coverage
    result.passing_evidence = sorted(
        ev_id
        for ev_id, info in per_evidence.items()
        if info["result"] == model.RESULT_PASS
    )

    # --- independence ------------------------------------------------------
    exception = None
    for exc in independence_exceptions:
        exc = _mapping(exc)
        if _text(exc.get("claim_id")) == claim_id:
            exception = exc
            break

    # Only evidence that actually supports the claim participates in the
    # independence assessment. Counting a failing or stale path as an
    # "independent second path" would let a broken check manufacture rigour.
    supporting = [
        per_evidence[ev_id]["entry"]
        for ev_id in result.passing_evidence
        if per_evidence[ev_id]["freshness"]["status"] == "CURRENT"
    ]
    assessment = indep_mod.assess_claim(supporting)
    result.independence = {
        "grade": assessment["grade"],
        "material_pair_count": assessment["material_pair_count"],
        "best_pair": assessment["best_pair"],
        "correlated_pairs": assessment["correlated_pairs"],
        "supporting_paths": [e["id"] for e in supporting],
    }

    if is_critical:
        if assessment["material_pair_count"] >= 1:
            pass
        elif exception is not None:
            missing = [
                key
                for key in ("justification", "accepted_by", "accepted_at")
                if not _text(exception.get(key))
            ]
            justification = _text(exception.get("justification"))
            if missing or len(justification) < MIN_INDEPENDENCE_EXCEPTION_LENGTH:
                findings.append(
                    Finding(
                        rule=RULE_INDEPENDENCE_EXCEPTION_INVALID,
                        severity=ERROR,
                        message=(
                            "independence exception is not adequately documented"
                            + (f"; missing {missing}" if missing else "")
                            + (
                                f"; justification is {len(justification)} chars, needs "
                                f">= {MIN_INDEPENDENCE_EXCEPTION_LENGTH}"
                                if len(justification) < MIN_INDEPENDENCE_EXCEPTION_LENGTH
                                else ""
                            )
                        ),
                    )
                )
            else:
                findings.append(
                    Finding(
                        rule=RULE_INSUFFICIENT_INDEPENDENT_PATHS,
                        severity=WARNING,
                        message=(
                            "fewer than two materially independent evidence paths, "
                            "accepted only because a documented exception exists "
                            f"(accepted by '{_text(exception.get('accepted_by'))}'); "
                            "this is recorded as residual uncertainty"
                        ),
                    )
                )
                result.residual_uncertainty.append(
                    "material independence requirement waived by documented "
                    f"exception accepted by {_text(exception.get('accepted_by'))}"
                )
        else:
            findings.append(
                Finding(
                    rule=RULE_INSUFFICIENT_INDEPENDENT_PATHS,
                    severity=ERROR,
                    message=(
                        "critical claim has fewer than two materially independent "
                        f"evidence paths (grade {assessment['grade']}); "
                        "two paths are not independent merely because they have "
                        "different names"
                    ),
                    detail={
                        "grade": assessment["grade"],
                        "supporting_paths": [e["id"] for e in supporting],
                        "best_pair": assessment["best_pair"],
                    },
                )
            )
    elif not supporting:
        findings.append(
            Finding(
                rule=RULE_EVIDENCE_RESULT_NOT_PASS,
                severity=ERROR,
                message="non-critical claim has no current passing evidence",
            )
        )

    # Correlated pairs are reported once, and only when no material pair was
    # found. When a material pair does exist, the correlated pairs are still
    # recorded in the independence block but are not worth a warning each --
    # otherwise a claim with many evidence paths would drown in repetition.
    if supporting and assessment["material_pair_count"] == 0:
        for pair in assessment["correlated_pairs"]:
            findings.append(
                Finding(
                    rule=RULE_CORRELATED_EVIDENCE,
                    severity=WARNING,
                    message=(
                        f"evidence '{pair['left']}' and '{pair['right']}' are "
                        f"correlated ({pair['grade']}): {pair['reason']}"
                    ),
                )
            )

    # --- evidence conflicts ------------------------------------------------
    conflicts_detected = _detect_conflicts(claim_id, fm_ids, per_evidence)
    # Evidence named in a complete, resolved conflict is superseded rather than
    # fatal: the disagreeing records must stay in the document so the conflict
    # remains traceable, and the resolution is the re-run evidence.
    superseded: set[str] = set()
    for detected in conflicts_detected:
        resolution = _find_resolution(conflicts, claim_id, detected["between"])
        if resolution is None:
            findings.append(
                Finding(
                    rule=RULE_EVIDENCE_CONFLICT_UNRESOLVED,
                    severity=ERROR,
                    message=(
                        "evidence paths disagree "
                        f"({detected['between'][0]}={detected['left_result']} vs "
                        f"{detected['between'][1]}={detected['right_result']}) on "
                        f"failure mode '{detected['failure_mode']}' and no resolution "
                        "is recorded; evidence conflicts must be root-caused and "
                        "re-run, never resolved by majority vote"
                    ),
                    subject=detected["failure_mode"],
                    detail=detected,
                )
            )
            continue

        missing = [
            key
            for key in ("root_cause", "correction", "re_run_evidence")
            if not _text(resolution.get(key))
        ]
        status = _text(resolution.get("status")).upper()
        if status != "RESOLVED" or missing:
            findings.append(
                Finding(
                    rule=RULE_CONFLICT_RESOLUTION_INCOMPLETE,
                    severity=ERROR,
                    message=(
                        f"conflict resolution for claim '{claim_id}' is incomplete "
                        f"(status={status!r}"
                        + (f", missing {missing}" if missing else "")
                        + ")"
                    ),
                    detail={"resolution": resolution},
                )
            )
            continue

        rerun_id = _text(resolution.get("re_run_evidence"))
        rerun = per_evidence.get(rerun_id)
        if rerun is None or rerun["result"] != model.RESULT_PASS:
            findings.append(
                Finding(
                    rule=RULE_CONFLICT_RESOLUTION_INCOMPLETE,
                    severity=ERROR,
                    message=(
                        f"conflict references re_run_evidence '{rerun_id}', which is "
                        "not a passing evidence path in this claim; resolution "
                        "requires new evidence after re-running"
                    ),
                    detail={"resolution": resolution},
                )
            )
            continue

        superseded.update(detected["between"])
        findings.append(
            Finding(
                rule=RULE_EVIDENCE_CONFLICT_RESOLVED,
                severity=INFO,
                message=(
                    "conflict between "
                    f"{detected['between'][0]} and {detected['between'][1]} on "
                    f"failure mode '{detected['failure_mode']}' is resolved by "
                    f"re-run evidence '{rerun_id}'"
                ),
                detail={"resolution": resolution, "conflict": detected},
            )
        )

    # --- non-PASS evidence, now that resolutions are known ------------------
    for ev_id, ev_result in non_pass_evidence:
        if ev_id in superseded:
            findings.append(
                Finding(
                    rule=RULE_EVIDENCE_RESULT_SUPERSEDED,
                    severity=WARNING,
                    message=(
                        f"evidence result is {ev_result} but it is named in a resolved "
                        "conflict and superseded by re-run evidence; retained so the "
                        "conflict stays traceable"
                    ),
                    subject=ev_id,
                )
            )
            continue
        findings.append(
            Finding(
                rule=RULE_EVIDENCE_RESULT_NOT_PASS,
                severity=ERROR,
                message=(
                    f"evidence result is {ev_result}; non-PASS evidence blocks the "
                    "claim that depends on it unless a resolved conflict explains it"
                ),
                subject=ev_id,
            )
        )

    # --- temporal ----------------------------------------------------------
    requires_temporal = bool(claim.get("requires_temporal")) or claim_type == "TEMPORAL"
    if requires_temporal:
        checks = [_mapping(c) for c in _sequence(claim.get("temporal_checks"))]
        kinds: list[str] = []
        for check in checks:
            kind = _text(check.get("kind"))
            if kind not in TEMPORAL_KINDS:
                findings.append(
                    Finding(
                        rule=RULE_TEMPORAL_CHECK_INVALID,
                        severity=ERROR,
                        message=(
                            f"temporal check kind {kind!r} is not one of "
                            f"{list(TEMPORAL_KINDS)}"
                        ),
                    )
                )
                continue
            if _text(check.get("result")).upper() != model.RESULT_PASS:
                findings.append(
                    Finding(
                        rule=RULE_TEMPORAL_CHECK_INVALID,
                        severity=ERROR,
                        message=f"temporal check '{kind}' did not pass",
                    )
                )
                continue
            kinds.append(kind)

        has_repetition = any(k in TEMPORAL_REPETITION_ANCHORS for k in kinds)
        has_durability = any(k in TEMPORAL_DURABILITY_ANCHORS for k in kinds)
        if not (has_repetition and has_durability):
            findings.append(
                Finding(
                    rule=RULE_TEMPORAL_CHECKS_MISSING,
                    severity=ERROR,
                    message=(
                        "temporal claim requires at least one repetition anchor "
                        f"(from {list(TEMPORAL_REPETITION_ANCHORS)}) and one durability "
                        f"anchor (from {list(TEMPORAL_DURABILITY_ANCHORS)}); found "
                        f"{sorted(set(kinds))}. A one-time success does not prove a "
                        "temporal claim"
                    ),
                    detail={"observed_kinds": sorted(set(kinds))},
                )
            )

    # --- world / user outcome ---------------------------------------------
    requires_world = bool(claim.get("requires_world_verification")) or claim_type == "OUTCOME"
    if requires_world:
        producer = _text(claim.get("producer"))
        external: list[str] = []
        self_reported: list[str] = []
        for ev_id, info in per_evidence.items():
            if info["result"] != model.RESULT_PASS:
                continue
            definition = _mapping(oracles.get(info["oracle"]))
            kind = _text(definition.get("kind"))
            dimensions = _mapping(info["entry"].get("dimensions"))
            impl = _text(dimensions.get("implementation"))
            is_self = kind == "internal" or (producer and impl == producer)
            if is_self:
                self_reported.append(ev_id)
            else:
                external.append(ev_id)

        if not external:
            findings.append(
                Finding(
                    rule=RULE_WORLD_VERIFICATION_MISSING,
                    severity=ERROR,
                    message=(
                        "claim asserts an effect outside the software but has no "
                        "passing evidence from an oracle that observes the world "
                        f"independently (external oracle kinds: "
                        f"{list(EXTERNAL_ORACLE_KINDS)})"
                    ),
                    detail={"self_reported_evidence": sorted(self_reported)},
                )
            )
        if self_reported:
            findings.append(
                Finding(
                    rule=RULE_WORLD_VERIFICATION_SELF_REPORTED,
                    severity=INFO,
                    message=(
                        "the producing application also reported its own external "
                        f"effect via {sorted(self_reported)}; that report is recorded "
                        "but never counted as the load-bearing evidence"
                    ),
                    detail={"self_reported_evidence": sorted(self_reported)},
                )
            )

        if claim_type == "OUTCOME":
            outcome_oracles = [
                ev_id
                for ev_id in external
                if _text(_mapping(oracles.get(per_evidence[ev_id]["oracle"])).get("kind"))
                in ("cross-modal", "external-reality")
            ]
            if not outcome_oracles:
                findings.append(
                    Finding(
                        rule=RULE_USER_OUTCOME_UNVERIFIED,
                        severity=ERROR,
                        message=(
                            "OUTCOME claim requires at least one passing evidence path "
                            "whose oracle is cross-modal or external-reality (something "
                            "that observes the user-visible result outside the "
                            "application), because technical correctness alone does not "
                            "establish that the user can achieve the intended outcome"
                        ),
                        detail={"external_evidence": sorted(external)},
                    )
                )

    # --- residual uncertainty ---------------------------------------------
    raw_uncertainty = _sequence(claim.get("residual_uncertainty"))
    uncertainty: list[str] = []
    for item in raw_uncertainty:
        if isinstance(item, dict):
            statement = _text(item.get("statement"))
            justification = _text(item.get("justification"))
            if statement.upper() == "NONE_KNOWN" and not justification:
                findings.append(
                    Finding(
                        rule=RULE_RESIDUAL_UNCERTAINTY_NOT_JUSTIFIED,
                        severity=ERROR,
                        message=(
                            "residual_uncertainty 'NONE_KNOWN' requires an explicit "
                            "justification; an unjustified empty field is not accepted"
                        ),
                    )
                )
            if statement:
                uncertainty.append(statement)
        else:
            text = _text(item)
            if text:
                uncertainty.append(text)

    if is_critical and not uncertainty:
        findings.append(
            Finding(
                rule=RULE_RESIDUAL_UNCERTAINTY_MISSING,
                severity=ERROR,
                message=(
                    "critical PASS must retain explicit residual uncertainty; add at "
                    "least one entry, or a NONE_KNOWN entry with a justification"
                ),
            )
        )
    result.residual_uncertainty.extend(uncertainty)

    # --- verdict ----------------------------------------------------------
    if result.blocking:
        result.status = "FAIL"
    else:
        result.status = "PASS"

    if result.status != "PASS":
        findings.append(
            Finding(
                rule=RULE_CLAIM_NOT_PASS,
                severity=ERROR,
                message=(
                    f"claim '{claim_id}' is not sufficiently supported "
                    f"({len(result.blocking)} blocking finding(s))"
                ),
            )
        )

    return result


def _detect_conflicts(
    claim_id: str, fm_ids: list[str], per_evidence: dict[str, dict[str, Any]]
) -> list[dict[str, Any]]:
    """Find failure modes on which passing evidence disagrees with other evidence."""
    detected: list[dict[str, Any]] = []
    for fm_id in fm_ids:
        results: list[tuple[str, str]] = []
        for ev_id, info in per_evidence.items():
            if fm_id in info["covers"] and info["result"] in (
                model.RESULT_PASS,
                model.RESULT_FAIL,
                model.RESULT_BLOCKED,
            ):
                results.append((ev_id, info["result"]))
        passes = [r for r in results if r[1] == model.RESULT_PASS]
        not_passes = [r for r in results if r[1] != model.RESULT_PASS]
        if passes and not_passes:
            left = passes[0]
            right = not_passes[0]
            detected.append(
                {
                    "claim_id": claim_id,
                    "failure_mode": fm_id,
                    "between": sorted([left[0], right[0]]),
                    "left_result": left[1],
                    "right_result": right[1],
                }
            )
    return detected


def _find_resolution(
    conflicts: list[Any], claim_id: str, between: list[str]
) -> dict[str, Any] | None:
    """Find a recorded resolution covering this conflict.

    Matching is order-insensitive but requires the resolution to name exactly the
    disagreeing pair, so a resolution cannot be reused to paper over a different
    conflict: an entry must name both parties to count.
    """
    for raw in conflicts:
        entry = _mapping(raw)
        if _text(entry.get("claim_id")) != claim_id:
            continue
        named = sorted(x for x in (_text(v) for v in _sequence(entry.get("between"))) if x)
        if named and named == sorted(between):
            return entry
    return None


def evaluate_freshness_cached(
    top_state: dict[str, Any], evidence: dict[str, Any], *, is_critical: bool
) -> dict[str, Any]:
    from .freshness import evaluate_freshness

    return evaluate_freshness(top_state, evidence, is_critical=is_critical)


# ---------------------------------------------------------------------------
# Document level
# ---------------------------------------------------------------------------


def validate(
    document: dict[str, Any],
    *,
    path: str = "",
    project_dir: str = "",
    allow_execute_oracles: bool = False,
    structural_findings: list[Finding] | None = None,
) -> ValidationResult:
    """Validate a loaded document and compute the gate verdict."""
    findings: list[Finding] = list(structural_findings or [])
    result = ValidationResult(document=document, path=path)

    oracles = _mapping(document.get("oracles"))
    top_state = _mapping(document.get("state"))
    conflicts = _sequence(document.get("conflicts"))
    indep_exceptions = _sequence(document.get("independence_exceptions"))
    crit_exceptions = _sequence(document.get("criticality_exceptions"))
    raw_claims = _sequence(document.get("claims"))

    # Duplicate claim ids are a document-level error: they make claim identity
    # ambiguous, which would make every later lookup unsound.
    seen_claims: set[str] = set()
    for index, claim in enumerate(raw_claims):
        cid = _text(_mapping(claim).get("id"))
        if not cid:
            continue
        if cid in seen_claims:
            findings.append(
                Finding(
                    rule=RULE_DUPLICATE_CLAIM_ID,
                    severity=ERROR,
                    message=f"claim id '{cid}' is declared more than once",
                    claim_id=cid,
                )
            )
        seen_claims.add(cid)

    if raw_claims and not top_state:
        findings.append(
            Finding(
                rule=RULE_STATE_MISSING,
                severity=ERROR,
                message=(
                    "document declares claims but records no 'state' block; without "
                    "an evaluated state, no evidence path can be shown to be current"
                ),
            )
        )

    claims: list[ClaimResult] = []
    for index, claim in enumerate(raw_claims):
        claim_result = _evaluate_claim(
            claim,
            index,
            oracles=oracles,
            top_state=top_state,
            conflicts=conflicts,
            independence_exceptions=indep_exceptions,
            criticality_exceptions=crit_exceptions,
            project_dir=project_dir,
            allow_execute=allow_execute_oracles,
        )
        claims.append(claim_result)

    result.claims = claims
    result.findings = findings

    all_findings = findings + [f for c in claims for f in c.findings]
    blocking = [f for f in all_findings if f.severity == ERROR]
    result.gate = "PASS" if not blocking else "FAIL"

    if result.gate == "FAIL":
        # GATE_FAIL is emitted once, as the aggregate, so a consumer that reads
        # only the top-level findings still sees the verdict.
        result.findings.insert(
            0,
            Finding(
                rule=RULE_GATE_FAIL,
                severity=ERROR,
                message=(
                    f"verification gate FAILED with {len(blocking)} blocking "
                    f"finding(s) across {len(claims)} claim(s)"
                ),
            ),
        )

    result.counts = _counts(result)
    return result


def _counts(result: ValidationResult) -> dict[str, int]:
    all_findings = result.all_findings()
    by_severity: dict[str, int] = {ERROR: 0, WARNING: 0, INFO: 0}
    for finding in all_findings:
        by_severity[finding.severity] = by_severity.get(finding.severity, 0) + 1

    return {
        "claims": len(result.claims),
        "claims_pass": sum(1 for c in result.claims if c.status == "PASS"),
        "claims_fail": sum(1 for c in result.claims if c.status != "PASS"),
        "critical_claims": sum(
            1 for c in result.claims if c.effective_criticality == model.CRITICAL
        ),
        "downgraded_claims": sum(1 for c in result.claims if c.downgraded),
        "evidence": sum(c.evidence_count for c in result.claims),
        "errors": by_severity.get(ERROR, 0),
        "warnings": by_severity.get(WARNING, 0),
        "infos": by_severity.get(INFO, 0),
    }
