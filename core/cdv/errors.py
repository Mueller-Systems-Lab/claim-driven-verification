"""Diagnostic model.

Every finding carries a stable machine-readable rule id. Rule ids are part of
the public contract: they are asserted by the canary suite and may be relied on
by future adapters. Do not rename a rule id without a schema version bump.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# Severity levels.
#   ERROR   - blocks PASS of the affected claim (and therefore the gate)
#   WARNING - does not block PASS but must remain visible in the report
#   INFO    - informational, recorded for audit
ERROR = "ERROR"
WARNING = "WARNING"
INFO = "INFO"


@dataclass(frozen=True)
class Finding:
    """A single diagnostic produced by the engine."""

    rule: str
    severity: str
    message: str
    claim_id: str | None = None
    subject: str | None = None
    detail: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "rule": self.rule,
            "severity": self.severity,
            "message": self.message,
        }
        if self.claim_id is not None:
            out["claim_id"] = self.claim_id
        if self.subject is not None:
            out["subject"] = self.subject
        if self.detail:
            out["detail"] = self.detail
        return out

    def render(self) -> str:
        loc = ""
        if self.claim_id and self.subject:
            loc = f" [{self.claim_id}/{self.subject}]"
        elif self.claim_id:
            loc = f" [{self.claim_id}]"
        elif self.subject:
            loc = f" [{self.subject}]"
        return f"{self.severity:7s} {self.rule}{loc}: {self.message}"


class CdvError(Exception):
    """Fatal, non-recoverable problem that prevents a verdict being computed.

    Raised for: unreadable input, malformed YAML, unsupported schema version,
    missing prerequisites. A CdvError always produces GATE=FAIL and a non-zero
    exit status. It is never downgraded to a warning.
    """

    def __init__(self, rule: str, message: str, **detail: Any) -> None:
        super().__init__(message)
        self.rule = rule
        self.message = message
        self.detail = detail


# --- Canonical rule identifiers -------------------------------------------
# Input / structural
RULE_INPUT_MISSING = "INPUT_FILE_MISSING"
RULE_YAML_MALFORMED = "YAML_MALFORMED"
RULE_ROOT_NOT_MAPPING = "ROOT_NOT_MAPPING"
RULE_SCHEMA_UNSUPPORTED = "SCHEMA_VERSION_UNSUPPORTED"
RULE_SCHEMA_MISSING_VERSION = "SCHEMA_VERSION_MISSING"
RULE_UNKNOWN_TOP_LEVEL_KEY = "UNKNOWN_TOP_LEVEL_KEY"
RULE_MISSING_REQUIRED_KEY = "MISSING_REQUIRED_KEY"

# State / freshness
RULE_STATE_MISSING = "STATE_NOT_RECORDED"
RULE_EVIDENCE_NOT_FINAL_STATE = "EVIDENCE_NOT_FINAL_STATE"
RULE_EVIDENCE_STATE_UNBOUND = "EVIDENCE_STATE_UNBOUND"
RULE_STALE_REUSE_NOT_JUSTIFIED = "STALE_REUSE_NOT_JUSTIFIED"

# Claims
RULE_CLAIM_MISSING_ID = "CLAIM_MISSING_ID"
RULE_DUPLICATE_CLAIM_ID = "DUPLICATE_CLAIM_ID"
RULE_CLAIM_MISSING_STATEMENT = "CLAIM_MISSING_STATEMENT"
RULE_CLAIM_MISSING_TYPE = "CLAIM_MISSING_TYPE"
RULE_CLAIM_TYPE_INVALID = "CLAIM_TYPE_INVALID"
RULE_CRITICALITY_MISSING = "CRITICALITY_MISSING"
RULE_CRITICALITY_INVALID = "CRITICALITY_INVALID"

# Criticality
RULE_NONCRITICAL_MISSING_RATIONALE = "NONCRITICAL_MISSING_RATIONALE"
RULE_CRITICALITY_DOWNGRADE_BLOCKED = "CRITICALITY_DOWNGRADE_BLOCKED"
RULE_CRITICALITY_REVIEW_INCOMPLETE = "CRITICALITY_REVIEW_INCOMPLETE"
RULE_CRITICALITY_TRIGGER_MATCHED = "CRITICALITY_TRIGGER_MATCHED"
RULE_ORACLE_QUALIFICATION_EXECUTED = "ORACLE_QUALIFICATION_EXECUTED"

# Failure modes
RULE_CRITICAL_CLAIM_WITHOUT_FAILURE_MODES = "CRITICAL_CLAIM_WITHOUT_FAILURE_MODES"
RULE_FAILURE_MODE_MISSING_ID = "FAILURE_MODE_MISSING_ID"
RULE_DUPLICATE_FAILURE_MODE_ID = "DUPLICATE_FAILURE_MODE_ID"
RULE_FAILURE_MODE_WITHOUT_ORACLE = "FAILURE_MODE_WITHOUT_ORACLE"
RULE_FAILURE_MODE_UNCOVERED = "FAILURE_MODE_UNCOVERED"

# Oracles
RULE_ORACLE_UNDEFINED = "ORACLE_UNDEFINED"
RULE_ORACLE_KIND_INVALID = "ORACLE_KIND_INVALID"
RULE_ORACLE_NOT_QUALIFIED = "ORACLE_NOT_QUALIFIED"
RULE_ORACLE_QUALIFICATION_BLOCKED = "ORACLE_QUALIFICATION_BLOCKED"
RULE_ORACLE_QUALIFICATION_UNVERIFIED = "ORACLE_QUALIFICATION_UNVERIFIED"

# Evidence
RULE_EVIDENCE_MISSING_ID = "EVIDENCE_MISSING_ID"
RULE_DUPLICATE_EVIDENCE_ID = "DUPLICATE_EVIDENCE_ID"
RULE_EVIDENCE_RESULT_INVALID = "EVIDENCE_RESULT_INVALID"
RULE_EVIDENCE_RESULT_NOT_PASS = "EVIDENCE_RESULT_NOT_PASS"
RULE_EVIDENCE_RESULT_SUPERSEDED = "EVIDENCE_RESULT_SUPERSEDED"
RULE_EVIDENCE_CONFLICT_RESOLVED = "EVIDENCE_CONFLICT_RESOLVED"
RULE_EVIDENCE_ORACLE_UNDEFINED = "EVIDENCE_ORACLE_UNDEFINED"
RULE_EVIDENCE_NO_FAILURE_MODE = "EVIDENCE_NO_FAILURE_MODE"
RULE_EVIDENCE_FAILURE_MODE_UNKNOWN = "EVIDENCE_FAILURE_MODE_UNKNOWN"

# Independence
RULE_INSUFFICIENT_INDEPENDENT_PATHS = "INSUFFICIENT_INDEPENDENT_PATHS"
RULE_CORRELATED_EVIDENCE = "CORRELATED_EVIDENCE"
RULE_INDEPENDENCE_EXCEPTION_INVALID = "INDEPENDENCE_EXCEPTION_INVALID"

# Conflicts
RULE_EVIDENCE_CONFLICT_UNRESOLVED = "EVIDENCE_CONFLICT_UNRESOLVED"
RULE_CONFLICT_RESOLUTION_INCOMPLETE = "CONFLICT_RESOLUTION_INCOMPLETE"

# Temporal
RULE_TEMPORAL_CHECKS_MISSING = "TEMPORAL_CHECKS_MISSING"
RULE_TEMPORAL_CHECK_INVALID = "TEMPORAL_CHECK_INVALID"

# World / user outcome
RULE_WORLD_VERIFICATION_MISSING = "WORLD_VERIFICATION_MISSING"
RULE_WORLD_VERIFICATION_SELF_REPORTED = "WORLD_VERIFICATION_SELF_REPORTED"
RULE_USER_OUTCOME_UNVERIFIED = "USER_OUTCOME_UNVERIFIED"

# Residual uncertainty
RULE_RESIDUAL_UNCERTAINTY_MISSING = "RESIDUAL_UNCERTAINTY_MISSING"
RULE_RESIDUAL_UNCERTAINTY_NOT_JUSTIFIED = "RESIDUAL_UNCERTAINTY_NOT_JUSTIFIED"

# Aggregate
RULE_CLAIM_NOT_PASS = "CLAIM_NOT_PASS"
RULE_GATE_FAIL = "GATE_FAIL"

# --- Verification context integrity (v1.0.1) -------------------------------
# A verification result is invalid unless the verifier can establish that it is
# observing the intended target, execution context and enforcement path. These
# are properties of the observation, not of the system under test, and they are
# checked before any behavioural evidence is interpreted.
RULE_CONTEXT_PROOF_MISSING = "CONTEXT_PROOF_MISSING"
RULE_CONTEXT_CONTRADICTION = "CONTEXT_CONTRADICTION"
RULE_CONTEXT_SENTINEL_STALE = "CONTEXT_SENTINEL_STALE"
RULE_CONTEXT_ENFORCEMENT_PATH_INACTIVE = "CONTEXT_ENFORCEMENT_PATH_INACTIVE"
RULE_CONTEXT_INSUFFICIENT_INDEPENDENT_INDICATORS = "CONTEXT_INSUFFICIENT_INDEPENDENT_INDICATORS"
RULE_CONTEXT_INHERITED_ONLY = "CONTEXT_INHERITED_ONLY"

# --- Oracle qualification assurance (v1.0.1) ------------------------------
RULE_ORACLE_QUALIFICATION_MODE_INVALID = "ORACLE_QUALIFICATION_MODE_INVALID"
RULE_ORACLE_DECLARED_MISSING_RATIONALE = "ORACLE_DECLARED_MISSING_RATIONALE"
RULE_ORACLE_DECLARED_MISSING_FEASIBILITY = "ORACLE_DECLARED_MISSING_FEASIBILITY"
RULE_ORACLE_ASSURANCE_DEGRADED = "ORACLE_ASSURANCE_DEGRADED"
RULE_ORACLE_EXECUTABLE_FEASIBLE_BUT_DECLARED = "ORACLE_EXECUTABLE_FEASIBLE_BUT_DECLARED"
RULE_ORACLE_EXECUTED_WITHOUT_COMMANDS = "ORACLE_EXECUTED_WITHOUT_COMMANDS"
