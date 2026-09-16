"""Canary definitions for the verification engine.

Design: differential testing.

There is one complete, passing baseline document. Every negative canary is the
baseline with **exactly one** documented mutation applied. The suite asserts two
things for each canary:

  * the baseline itself yields GATE=PASS  (positive canary)
  * the mutated document yields GATE=FAIL, and the failure names the specific
    rule that the mutation was designed to violate

The second assertion alone would be weak: a gate that always failed would pass
it. The first assertion alone would be weak: it says nothing about detection.
Together they show the gate discriminates, and that each individual defect is
detected by the rule meant to detect it rather than by an unrelated accident.

A canary that fails for the *wrong* rule is treated as a failure of the canary
itself (``unexpected``), not as a pass.
"""

from __future__ import annotations

import copy

# ---------------------------------------------------------------------------
# Baseline: a complete, correctly evidenced critical claim. This must PASS.
# ---------------------------------------------------------------------------

BASELINE: dict = {
    "version": 1,
    "project": {"name": "canary-baseline"},
    "state": {"commit": "TESTCOMMIT", "tree_hash": "TESTTREE", "environment": "canary"},
    "oracles": {
        "filesystem-readback": {
            "kind": "independent-technical",
            "description": "read the produced artifact back from the filesystem",
            "qualification": {
                "mode": "declared",
                "positive_case": {"description": "real file present", "result": "PASS"},
                "negative_case": {"description": "no file present", "result": "DETECTED"},
            },
        },
        "external-pdf-parser": {
            "kind": "external-reality",
            "description": "parse the artifact with a parser the producer does not own",
            "qualification": {
                "mode": "declared",
                "positive_case": {"description": "valid pdf", "result": "PASS"},
                "negative_case": {"description": "truncated pdf rejected", "result": "DETECTED"},
            },
        },
    },
    "claims": [
        {
            "id": "PDF-EXPORT-001",
            "statement": "Confirmed user data is exported into a valid PDF file.",
            "type": "ARTIFACT",
            "criticality": "CRITICAL",
            "producer": "report-writer",
            "failure_modes": [
                {"id": "NO_FILE_CREATED", "oracle": "filesystem-readback"},
                {"id": "INVALID_PDF", "oracle": "external-pdf-parser"},
            ],
            "evidence": [
                {
                    "id": "EV-001",
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
                },
                {
                    "id": "EV-002",
                    "oracle": "external-pdf-parser",
                    "result": "PASS",
                    "failure_modes": ["INVALID_PDF"],
                    "dimensions": {
                        "model": "none",
                        "tool": "pdfinfo",
                        "implementation": "external-pdf-parser",
                        "runtime": "cpython",
                        "data_source": "produced-pdf",
                        "observation_channel": "independent-parser",
                        "specification_source": "pdf-specification",
                    },
                    "observed_state": {"commit": "TESTCOMMIT", "tree_hash": "TESTTREE"},
                },
            ],
            "residual_uncertainty": [
                "printer-specific rendering was not exercised on physical hardware"
            ],
        }
    ],
}


def _claim(doc: dict, claim_id: str) -> dict:
    for claim in doc["claims"]:
        if claim["id"] == claim_id:
            return claim
    raise KeyError(claim_id)


def _evidence(claim: dict, ev_id: str) -> dict:
    for ev in claim["evidence"]:
        if ev["id"] == ev_id:
            return ev
    raise KeyError(ev_id)


def _failure_mode(claim: dict, fm_id: str) -> dict:
    for fm in claim["failure_modes"]:
        if fm["id"] == fm_id:
            return fm
    raise KeyError(fm_id)


# ---------------------------------------------------------------------------
# Mutations
# ---------------------------------------------------------------------------


def mutate_no_failure_modes(doc: dict) -> None:
    """A critical claim that declares no failure modes at all."""
    del _claim(doc, "PDF-EXPORT-001")["failure_modes"]


def mutate_failure_mode_without_oracle(doc: dict) -> None:
    """A failure mode with no oracle: nothing decides whether it occurred."""
    del _failure_mode(_claim(doc, "PDF-EXPORT-001"), "INVALID_PDF")["oracle"]


def mutate_single_evidence_path(doc: dict) -> None:
    """Only one evidence path: nothing corroborates it."""
    claim = _claim(doc, "PDF-EXPORT-001")
    claim["failure_modes"] = [fm for fm in claim["failure_modes"] if fm["id"] == "NO_FILE_CREATED"]
    claim["evidence"] = [ev for ev in claim["evidence"] if ev["id"] == "EV-001"]


def mutate_correlated_evidence(doc: dict) -> None:
    """Two paths that differ only in tool name, presented as if independent."""
    claim = _claim(doc, "PDF-EXPORT-001")
    claim["failure_modes"] = [{"id": "NO_FILE_CREATED", "oracle": "filesystem-readback"}]
    claim["evidence"] = [ev for ev in claim["evidence"] if ev["id"] == "EV-001"]
    second = copy.deepcopy(_evidence(claim, "EV-001"))
    second["id"] = "EV-001B"
    # The ONLY difference is the tool name. Every load-bearing dimension -- the
    # model, the implementation, the runtime, and the whole observation axis --
    # is identical, so a failure of the underlying mechanism would take out both
    # paths at once.
    second["dimensions"]["tool"] = "different-tool-name"
    claim["evidence"].append(second)


def mutate_unresolved_conflict(doc: dict) -> None:
    """Two paths disagree and no root-caused resolution is recorded."""
    claim = _claim(doc, "PDF-EXPORT-001")
    third = copy.deepcopy(_evidence(claim, "EV-001"))
    third["id"] = "EV-003"
    third["result"] = "FAIL"
    third["dimensions"]["implementation"] = "second-filesystem-readback"
    third["dimensions"]["runtime"] = "cpython"
    third["dimensions"]["observation_channel"] = "filesystem-via-python"
    third["dimensions"]["data_source"] = "inode-table"
    third["dimensions"]["specification_source"] = "posix-stat"
    claim["evidence"].append(third)


def mutate_stale_evidence(doc: dict) -> None:
    """Evidence observed against a different state than the one under evaluation."""
    _evidence(_claim(doc, "PDF-EXPORT-001"), "EV-001")["observed_state"] = {
        "commit": "OLDERCOMMIT",
        "tree_hash": "OLDERTREE",
    }


def mutate_unqualified_oracle(doc: dict) -> None:
    """A critical oracle with no qualification block at all."""
    del doc["oracles"]["filesystem-readback"]["qualification"]


def mutate_oracle_never_rejected_anything(doc: dict) -> None:
    """A 'qualified' oracle whose negative case was never shown to be detected."""
    doc["oracles"]["filesystem-readback"]["qualification"]["negative_case"]["result"] = (
        "NOT_DETECTED"
    )


def mutate_missing_residual_uncertainty(doc: dict) -> None:
    del _claim(doc, "PDF-EXPORT-001")["residual_uncertainty"]


def mutate_unevidenced_none_known(doc: dict) -> None:
    """An empty uncertainty field laundered through a NONE_KNOWN keyword."""
    _claim(doc, "PDF-EXPORT-001")["residual_uncertainty"] = [{"statement": "NONE_KNOWN"}]


def mutate_fake_noncritical_downgrade(doc: dict) -> None:
    """Relabel a money/artifact claim NON_CRITICAL to shrink the evidence bar."""
    claim = _claim(doc, "PDF-EXPORT-001")
    claim["criticality"] = "NON_CRITICAL"
    claim["criticality_rationale"] = (
        "This is only a convenience feature and the team agreed it is not important "
        "enough to warrant the full critical verification path."
    )
    # Also drop one evidence path, which is exactly what the downgrade is for:
    # if the relabel were honoured, one path would now be enough.
    claim["failure_modes"] = [fm for fm in claim["failure_modes"] if fm["id"] == "NO_FILE_CREATED"]
    claim["evidence"] = [ev for ev in claim["evidence"] if ev["id"] == "EV-001"]


def mutate_temporal_without_durability(doc: dict) -> None:
    """A temporal claim supported only by a single repeated run."""
    claim = _claim(doc, "PDF-EXPORT-001")
    claim["statement"] = "Exported PDF files survive an application restart."
    claim["type"] = "TEMPORAL"
    claim["temporal_checks"] = [{"kind": "repeated-execution", "result": "PASS"}]


def mutate_world_claim_self_reported(doc: dict) -> None:
    """An outcome claim proven only by the application's own success message."""
    claim = _claim(doc, "PDF-EXPORT-001")
    claim["statement"] = "The user can open the exported PDF outside the application."
    claim["type"] = "OUTCOME"
    doc["oracles"]["self-report"] = {
        "kind": "internal",
        "description": "the application asserts that it wrote the file",
        "qualification": {
            "mode": "declared",
            "positive_case": {"result": "PASS"},
            "negative_case": {"result": "DETECTED"},
        },
    }
    claim["failure_modes"] = [{"id": "APP_SAYS_IT_WORKED", "oracle": "self-report"}]
    claim["evidence"] = [
        {
            "id": "EV-SELFREPORT",
            "oracle": "self-report",
            "result": "PASS",
            "failure_modes": ["APP_SAYS_IT_WORKED"],
            "dimensions": {
                "model": "none",
                "tool": "app-log",
                "implementation": "report-writer",
                "runtime": "node",
                "data_source": "application-log",
                "observation_channel": "application-own-report",
                "specification_source": "application-docs",
            },
            "observed_state": {"commit": "TESTCOMMIT", "tree_hash": "TESTTREE"},
        }
    ]


# ---------------------------------------------------------------------------
# Canary registry
# ---------------------------------------------------------------------------
# (name, mutation-or-None, expected_gate, expected_rule, expected_rule_severity)
# mutation None means "the baseline as-is" (the positive canary).

CANARIES: list[tuple[str, object, str, str | None, str]] = [
    ("baseline_positive", None, "PASS", None, "INFO"),
    ("no_failure_modes", mutate_no_failure_modes, "FAIL",
     "CRITICAL_CLAIM_WITHOUT_FAILURE_MODES", "ERROR"),
    ("failure_mode_without_oracle", mutate_failure_mode_without_oracle, "FAIL",
     "FAILURE_MODE_WITHOUT_ORACLE", "ERROR"),
    ("single_evidence_path", mutate_single_evidence_path, "FAIL",
     "INSUFFICIENT_INDEPENDENT_PATHS", "ERROR"),
    ("correlated_evidence", mutate_correlated_evidence, "FAIL",
     "INSUFFICIENT_INDEPENDENT_PATHS", "ERROR"),
    ("correlated_evidence_warning", mutate_correlated_evidence, "FAIL",
     "CORRELATED_EVIDENCE", "WARNING"),
    ("unresolved_conflict", mutate_unresolved_conflict, "FAIL",
     "EVIDENCE_CONFLICT_UNRESOLVED", "ERROR"),
    ("stale_evidence", mutate_stale_evidence, "FAIL",
     "EVIDENCE_NOT_FINAL_STATE", "ERROR"),
    ("unqualified_oracle", mutate_unqualified_oracle, "FAIL",
     "ORACLE_NOT_QUALIFIED", "ERROR"),
    ("oracle_never_rejected_anything", mutate_oracle_never_rejected_anything, "FAIL",
     "ORACLE_QUALIFICATION_BLOCKED", "ERROR"),
    ("missing_residual_uncertainty", mutate_missing_residual_uncertainty, "FAIL",
     "RESIDUAL_UNCERTAINTY_MISSING", "ERROR"),
    ("unevidenced_none_known", mutate_unevidenced_none_known, "FAIL",
     "RESIDUAL_UNCERTAINTY_NOT_JUSTIFIED", "ERROR"),
    ("fake_noncritical_downgrade", mutate_fake_noncritical_downgrade, "FAIL",
     "CRITICALITY_REVIEW_INCOMPLETE", "ERROR"),
    ("temporal_without_durability", mutate_temporal_without_durability, "FAIL",
     "TEMPORAL_CHECKS_MISSING", "ERROR"),
    ("world_claim_self_reported", mutate_world_claim_self_reported, "FAIL",
     "WORLD_VERIFICATION_MISSING", "ERROR"),
]


def build(name: str, mutation) -> dict:
    """Return the baseline with ``mutation`` applied (or the baseline itself)."""
    doc = copy.deepcopy(BASELINE)
    if mutation is not None:
        mutation(doc)
    return doc
