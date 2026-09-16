"""Material-independence analysis.

Design constraint from the specification: independence must NOT be determined
by counting tools, and must NOT be a subjective declaration made by the agent
that produced the evidence. It is therefore derived deterministically from
recorded dimensions.

The central rule:

    Two evidence paths are materially independent only if they differ on BOTH
    the *generation* axis and the *observation* axis.

Rationale
---------
* Generation axis (``model``, ``implementation``, ``runtime``) captures "was the
  thing that produced the result built by the same machinery?"  Two paths that
  share a model, an implementation and a runtime share their material failure
  causes, no matter how differently they are named.
* Observation axis (``data_source``, ``observation_channel``,
  ``specification_source``) captures "did we look at the world through the same
  window and against the same reference?"  Reading the same file twice through
  two thin wrappers is one observation, not two.

``tool`` is deliberately excluded from both axes. Naming two different tools is
the cheapest possible way to fake independence, and the specification calls this
out explicitly. ``tool`` differences are recorded for audit but never count.

Grading is qualitative only (LOW / MEDIUM / HIGH). No numeric probability is
produced, because no empirical calibration exists to justify one.
"""

from __future__ import annotations

from dataclasses import dataclass
from itertools import combinations
from typing import Any, Iterable

# Dimensions recorded per evidence path.
GENERATION_AXIS = ("model", "implementation", "runtime")
OBSERVATION_AXIS = ("data_source", "observation_channel", "specification_source")

# Recorded but non-independence-bearing.
AUDIT_ONLY_DIMENSIONS = ("tool",)

ALL_DIMENSIONS = GENERATION_AXIS + OBSERVATION_AXIS + AUDIT_ONLY_DIMENSIONS

LOW = "LOW"
MEDIUM = "MEDIUM"
HIGH = "HIGH"

# A pair must reach at least this grade to count toward the two-independent-path
# requirement.
MATERIAL_THRESHOLD = MEDIUM


@dataclass(frozen=True)
class PairAssessment:
    """Independence assessment for one unordered pair of evidence paths."""

    left: str
    right: str
    grade: str
    generation_differences: tuple[str, ...]
    observation_differences: tuple[str, ...]
    audit_only_differences: tuple[str, ...]
    material: bool
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "left": self.left,
            "right": self.right,
            "grade": self.grade,
            "material": self.material,
            "generation_differences": list(self.generation_differences),
            "observation_differences": list(self.observation_differences),
            "audit_only_differences": list(self.audit_only_differences),
            "reason": self.reason,
        }


def _normalise(value: Any) -> str:
    """Normalise a dimension value for comparison.

    Missing/empty values normalise to a single sentinel so that "both paths
    left this blank" counts as *shared*, and "one path blank, one filled"
    counts as a difference. Blank-matching-blank must never be able to
    manufacture independence.
    """
    if value is None:
        return "\x00UNSET"
    text = str(value).strip()
    return text if text else "\x00UNSET"


def _dimensions_of(evidence: dict[str, Any]) -> dict[str, str]:
    """Extract the independence dimensions from one evidence entry.

    Values are read from a nested ``dimensions`` mapping. A flat fallback is
    intentionally NOT provided: silently accepting top-level keys would let a
    malformed entry appear to have recorded dimensions when it has not.
    """
    raw = evidence.get("dimensions")
    if not isinstance(raw, dict):
        raw = {}
    return {dim: _normalise(raw.get(dim)) for dim in ALL_DIMENSIONS}


def assess_pair(
    left_id: str, left: dict[str, Any], right_id: str, right: dict[str, Any]
) -> PairAssessment:
    """Deterministically assess whether two evidence paths are independent."""
    a = _dimensions_of(left)
    b = _dimensions_of(right)

    differing = [dim for dim in ALL_DIMENSIONS if a[dim] != b[dim]]
    gen = tuple(dim for dim in GENERATION_AXIS if dim in differing)
    obs = tuple(dim for dim in OBSERVATION_AXIS if dim in differing)
    audit = tuple(dim for dim in AUDIT_ONLY_DIMENSIONS if dim in differing)

    if not gen and not obs:
        grade = LOW
        material = False
        if audit:
            reason = (
                "paths differ only in their tool name; tool naming is explicitly "
                "not evidence of independence"
            )
        else:
            reason = (
                "paths record the same generation and observation dimensions, so "
                "their material failure causes are shared"
            )
    elif not gen:
        grade = LOW
        material = False
        reason = (
            "paths differ on the observation axis but share model, implementation "
            "and runtime; the producing machinery has a single shared failure cause"
        )
    elif not obs:
        grade = LOW
        material = False
        reason = (
            "paths differ on the generation axis but share data source, observation "
            "channel and specification source; this is one observation of one world "
            "state, not two"
        )
    else:
        k = len(gen) + len(obs)
        grade = MEDIUM if k <= 2 else HIGH
        material = True
        reason = (
            f"paths differ across both axes ({k} load-bearing dimension(s)); "
            "distinct production machinery and distinct observation"
        )

    return PairAssessment(
        left=left_id,
        right=right_id,
        grade=grade,
        generation_differences=gen,
        observation_differences=obs,
        audit_only_differences=audit,
        material=material,
        reason=reason,
    )


def assess_claim(evidence: Iterable[dict[str, Any]]) -> dict[str, Any]:
    """Assess all pairs among a claim's evidence entries.

    Returns the best pair, all correlated pairs, and the claim-level grade.
    ``evidence`` entries must each carry ``id``.
    """
    items = [e for e in evidence if isinstance(e, dict)]
    ids = [str(e.get("id", f"<unnamed#{i}>")) for i, e in enumerate(items)]

    pairs: list[PairAssessment] = []
    for i, j in combinations(range(len(items)), 2):
        pairs.append(assess_pair(ids[i], items[i], ids[j], items[j]))

    material_pairs = [p for p in pairs if p.material]
    correlated = [p for p in pairs if not p.material]

    if material_pairs:
        best = max(material_pairs, key=lambda p: (p.grade == HIGH, len(p.generation_differences) + len(p.observation_differences)))
        grade = best.grade
    else:
        grade = LOW
        best = None

    return {
        "grade": grade,
        "material_pair_count": len(material_pairs),
        "best_pair": best.to_dict() if best else None,
        "correlated_pairs": [p.to_dict() for p in correlated],
        "pairs": [p.to_dict() for p in pairs],
    }
