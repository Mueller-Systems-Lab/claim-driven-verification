"""Deterministic summary rendering.

Two renderings of the same verdict:

* ``key=value`` lines, stable and diffable, consumed by the installer and by
  any shell-based independent readback.
* JSON, consumed by adapters and future tooling.

Neither rendering invents information. Every line is a direct function of the
findings produced by :mod:`cdv.gates`.
"""

from __future__ import annotations

import json
from typing import Any

from . import __version__, SCHEMA_VERSION
from .errors import ERROR, WARNING, Finding
from .gates import ValidationResult

# Rule families aggregated into the named gate dimensions of the bootstrap
# report. A dimension FAILS if any ERROR in its family is present.
GATE_DIMENSIONS: dict[str, tuple[str, ...]] = {
    "INDEPENDENCE": (
        "INSUFFICIENT_INDEPENDENT_PATHS",
        "INDEPENDENCE_EXCEPTION_INVALID",
        "CORRELATED_EVIDENCE",
    ),
    "ORACLE_QUALIFICATION": (
        "ORACLE_NOT_QUALIFIED",
        "ORACLE_QUALIFICATION_BLOCKED",
        "ORACLE_QUALIFICATION_UNVERIFIED",
        "ORACLE_KIND_INVALID",
        "ORACLE_UNDEFINED",
    ),
    "CONFLICT": (
        "EVIDENCE_CONFLICT_UNRESOLVED",
        "CONFLICT_RESOLUTION_INCOMPLETE",
    ),
    "FRESHNESS": (
        "EVIDENCE_NOT_FINAL_STATE",
        "EVIDENCE_STATE_UNBOUND",
        "STATE_NOT_RECORDED",
        "STALE_REUSE_NOT_JUSTIFIED",
    ),
    "RESIDUAL_UNCERTAINTY": (
        "RESIDUAL_UNCERTAINTY_MISSING",
        "RESIDUAL_UNCERTAINTY_NOT_JUSTIFIED",
    ),
}


def dimension_status(result: ValidationResult, dimension: str) -> str:
    """PASS / FAIL / NOT_EXERCISED for one gate dimension.

    ``NOT_EXERCISED`` is reported rather than a vacuous PASS when the document
    contains no claims at all: a dimension that was never given anything to
    check has not been demonstrated to work.
    """
    rules = set(GATE_DIMENSIONS[dimension])
    findings = result.all_findings()
    if any(f.severity == ERROR and f.rule in rules for f in findings):
        return "FAIL"
    if not result.claims:
        return "NOT_EXERCISED"
    return "PASS"


def dimensions(result: ValidationResult) -> dict[str, str]:
    return {name: dimension_status(result, name) for name in GATE_DIMENSIONS}


def render_keyvalues(result: ValidationResult) -> str:
    """Stable key=value rendering."""
    dims = dimensions(result)
    counts = result.counts

    lines = [
        f"CDV_ENGINE_VERSION={__version__}",
        f"CDV_SCHEMA_VERSION={SCHEMA_VERSION}",
        f"CDV_DOCUMENT={result.path}",
        f"CDV_GATE={result.gate}",
        f"CDV_SCHEMA_STATUS={'OK' if not _has(result, {'SCHEMA_VERSION_UNSUPPORTED', 'SCHEMA_VERSION_MISSING', 'YAML_MALFORMED', 'ROOT_NOT_MAPPING', 'UNKNOWN_TOP_LEVEL_KEY', 'MISSING_REQUIRED_KEY'}) else 'INVALID'}",
        f"CDV_INDEPENDENCE_GATE={dims['INDEPENDENCE']}",
        f"CDV_ORACLE_QUALIFICATION_GATE={dims['ORACLE_QUALIFICATION']}",
        f"CDV_CONFLICT_GATE={dims['CONFLICT']}",
        f"CDV_FRESHNESS_GATE={dims['FRESHNESS']}",
        f"CDV_RESIDUAL_UNCERTAINTY_GATE={dims['RESIDUAL_UNCERTAINTY']}",
        f"CDV_CLAIMS={counts.get('claims', 0)}",
        f"CDV_CLAIMS_PASS={counts.get('claims_pass', 0)}",
        f"CDV_CLAIMS_FAIL={counts.get('claims_fail', 0)}",
        f"CDV_CRITICAL_CLAIMS={counts.get('critical_claims', 0)}",
        f"CDV_DOWNGRADED_CLAIMS={counts.get('downgraded_claims', 0)}",
        f"CDV_EVIDENCE={counts.get('evidence', 0)}",
        f"CDV_ERRORS={counts.get('errors', 0)}",
        f"CDV_WARNINGS={counts.get('warnings', 0)}",
        f"CDV_RESIDUAL_UNCERTAINTY={len(_all_uncertainty(result))}",
    ]
    if result.findings and result.findings[0].rule == "GATE_FAIL":
        lines.append(f"CDV_FIRST_BLOCKING_RULE={_first_blocking_rule(result) or 'NONE'}")
    return "\n".join(lines)


def _has(result: ValidationResult, rules: set[str]) -> bool:
    return any(f.severity == ERROR and f.rule in rules for f in result.all_findings())


def _first_blocking_rule(result: ValidationResult) -> str | None:
    for finding in result.all_findings():
        if finding.severity == ERROR and finding.rule != "GATE_FAIL":
            return finding.rule
    return None


def _all_uncertainty(result: ValidationResult) -> list[str]:
    out: list[str] = []
    for claim in result.claims:
        out.extend(claim.residual_uncertainty)
    return out


def render_text(result: ValidationResult, *, verbose: bool = False) -> str:
    """Human-readable report."""
    lines: list[str] = []
    lines.append("=" * 72)
    lines.append("CLAIM-DRIVEN VERIFICATION REPORT")
    lines.append("=" * 72)
    lines.append(f"document   : {result.path}")
    lines.append(f"engine     : cdv {__version__} (schema v{SCHEMA_VERSION})")
    lines.append(f"gate       : {result.gate}")
    lines.append("-" * 72)

    for claim in result.claims:
        marker = "PASS" if claim.status == "PASS" else "FAIL"
        lines.append(
            f"[{marker}] {claim.claim_id}  ({claim.claim_type}, "
            f"effective={claim.effective_criticality}"
            + (f", declared={claim.declared_criticality}, DOWNGRADED" if claim.downgraded else "")
            + ")"
        )
        if claim.statement:
            lines.append(f"        {claim.statement}")
        ind = claim.independence
        lines.append(
            f"        evidence={claim.evidence_count} passing={len(claim.passing_evidence)} "
            f"independence={ind.get('grade')} material_pairs={ind.get('material_pair_count')}"
        )
        covered = sum(1 for fm, evs in claim.coverage.items() if evs)
        total = len(claim.coverage)
        if total:
            lines.append(f"        failure-mode coverage: {covered}/{total}")
        for uncertainty in claim.residual_uncertainty:
            lines.append(f"        residual uncertainty: {uncertainty}")
        for finding in claim.findings:
            if finding.severity == ERROR or verbose:
                lines.append(f"        {finding.render()}")
        lines.append("")

    if result.findings:
        lines.append("-" * 72)
        lines.append("document findings:")
        for finding in result.findings:
            if finding.severity == ERROR or verbose:
                lines.append(f"  {finding.render()}")
        lines.append("")

    lines.append("-" * 72)
    dims = dimensions(result)
    for name in GATE_DIMENSIONS:
        lines.append(f"  {name:26s} {dims[name]}")
    counts = result.counts
    lines.append(
        f"  claims={counts.get('claims',0)} pass={counts.get('claims_pass',0)} "
        f"fail={counts.get('claims_fail',0)} errors={counts.get('errors',0)} "
        f"warnings={counts.get('warnings',0)}"
    )
    lines.append("=" * 72)
    lines.append(f"CDV_GATE={result.gate}")
    lines.append("=" * 72)
    return "\n".join(lines)


def render_json(result: ValidationResult) -> str:
    payload: dict[str, Any] = result.to_dict()
    payload["engine_version"] = __version__
    payload["schema_version"] = SCHEMA_VERSION
    payload["dimensions"] = dimensions(result)
    return json.dumps(payload, indent=2, sort_keys=False)


def render_findings_only(result: ValidationResult) -> str:
    """One line per finding: severity|rule|claim|subject|message.

    Pipe-delimited so that a shell-based independent readback can grep and count
    without parsing the human report.
    """
    rows: list[str] = []
    for finding in result.all_findings():
        rows.append(
            "|".join(
                [
                    finding.severity,
                    finding.rule,
                    finding.claim_id or "-",
                    finding.subject or "-",
                    finding.message.replace("\n", " "),
                ]
            )
        )
    return "\n".join(rows)
