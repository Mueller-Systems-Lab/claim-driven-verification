"""Evidence freshness and final-state binding.

Deliberately simple and auditable, per the specification: no speculative
dependency graph, just "is this evidence about the state we are actually
judging?".

A project records the state under evaluation once, at the top level::

    state:
      commit: 4f2a1c9...
      tree_hash: 9ab3...
      environment: {python: "3.12.3", os: "linux"}

Each evidence entry records what it observed::

    observed_state:
      commit: 4f2a1c9...
      tree_hash: 9ab3...
      artifact_hash: sha256:...

An evidence path is current when its ``observed_state`` agrees with the
top-level ``state`` on every key the evidence recorded, and it recorded at least
one identifying key (``commit`` or ``tree_hash``).

Evidence that recorded nothing is ``EVIDENCE_STATE_UNBOUND``: an unbound
evidence path cannot be shown to describe the final state, so it cannot carry a
critical claim.

A project may explicitly allow reuse of older evidence via
``reuse_across_states: {justification: ..., accepted_by: ...}``. Reuse is
restricted to NON_CRITICAL claims; for a CRITICAL claim it is refused outright.
That asymmetry is the point of the rule: for a critical claim, evidence about a
different state is not weak evidence, it is not evidence.
"""

from __future__ import annotations

from typing import Any

IDENTIFYING_KEYS = ("commit", "tree_hash")


def _as_mapping(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _stringify(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def evaluate_freshness(
    top_state: dict[str, Any],
    evidence: dict[str, Any],
    *,
    is_critical: bool,
) -> dict[str, Any]:
    """Assess one evidence entry against the state under evaluation.

    Returns a dict with keys: ``status`` ("CURRENT" | "STALE" | "UNBOUND"),
    ``mismatches``, ``reuse_accepted``, ``explanation``.
    """
    state = _as_mapping(top_state)
    observed = _as_mapping(evidence.get("observed_state"))

    recorded = {k: _stringify(observed.get(k)) for k in IDENTIFYING_KEYS}
    if not any(recorded.values()):
        return {
            "status": "UNBOUND",
            "mismatches": [],
            "reuse_accepted": False,
            "explanation": (
                "evidence records no observed_state.commit or "
                "observed_state.tree_hash, so it cannot be shown to describe the "
                "state under evaluation"
            ),
        }

    mismatches: list[dict[str, str]] = []
    for key in IDENTIFYING_KEYS:
        want = _stringify(state.get(key))
        got = recorded[key]
        if want is not None and got is not None and want != got:
            mismatches.append({"key": key, "expected": want, "observed": got})

    if not mismatches:
        return {
            "status": "CURRENT",
            "mismatches": [],
            "reuse_accepted": False,
            "explanation": "evidence observed_state agrees with the evaluated state",
        }

    reuse = _as_mapping(evidence.get("reuse_across_states"))
    justification = _stringify(reuse.get("justification"))
    accepted_by = _stringify(reuse.get("accepted_by"))

    if is_critical:
        return {
            "status": "STALE",
            "mismatches": mismatches,
            "reuse_accepted": False,
            "explanation": (
                "evidence was observed against a different state and this claim is "
                "CRITICAL; stale reuse is not permitted for critical claims"
            ),
        }

    if justification and accepted_by:
        return {
            "status": "STALE",
            "mismatches": mismatches,
            "reuse_accepted": True,
            "explanation": (
                f"evidence is stale but reuse was explicitly accepted by "
                f"'{accepted_by}': {justification}"
            ),
        }

    return {
        "status": "STALE",
        "mismatches": mismatches,
        "reuse_accepted": False,
        "explanation": (
            "evidence was observed against a different state and no "
            "reuse_across_states justification/accepted_by pair was recorded"
        ),
    }
