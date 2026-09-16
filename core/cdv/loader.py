"""Loading and structural validation of verification.yaml.

Dependency policy
-----------------
PyYAML is the single external prerequisite. It is declared in preflight and its
absence is a fail-closed condition rather than a silent fallback to a
hand-rolled parser: a second, weaker parser would be a second set of failure
modes for the one file whose correct interpretation the whole system rests on.

Structural checks here are deliberately minimal. They establish that the
document is shaped like a verification contract. *Semantic* obligations (does a
critical claim have failure modes, are its oracles qualified, is its evidence
independent) live in ``gates.py``.
"""

from __future__ import annotations

import os
from typing import Any

from . import SCHEMA_VERSION
from .errors import (
    RULE_MISSING_REQUIRED_KEY,
    RULE_ROOT_NOT_MAPPING,
    RULE_SCHEMA_MISSING_VERSION,
    RULE_SCHEMA_UNSUPPORTED,
    RULE_UNKNOWN_TOP_LEVEL_KEY,
    RULE_YAML_MALFORMED,
    RULE_INPUT_MISSING,
    ERROR,
    Finding,
    CdvError,
)

try:  # pragma: no cover - exercised via the missing-dependency preflight test
    import yaml
except ImportError:  # pragma: no cover
    yaml = None  # type: ignore[assignment]

# The canonical file name. A project may point the engine at another path, but
# this is what the installer writes and what adapters look for.
DEFAULT_FILENAME = "verification.yaml"

KNOWN_TOP_LEVEL_KEYS = frozenset(
    {
        "version",
        "project",
        "state",
        "integration",
        "oracles",
        "claims",
        "conflicts",
        "independence_exceptions",
        "criticality_exceptions",
        "notes",
    }
)

REQUIRED_TOP_LEVEL_KEYS = ("version", "claims")


def require_yaml() -> None:
    """Fail closed when PyYAML is unavailable."""
    if yaml is None:  # pragma: no cover
        raise CdvError(
            "DEPENDENCY_MISSING_PYYAML",
            "PyYAML is required to read verification.yaml and is not importable. "
            "Install it (python3 -m pip install PyYAML) and re-run. Refusing to "
            "fall back to an approximate parser for the canonical verification "
            "document.",
            dependency="PyYAML",
        )


def load_document(path: str) -> tuple[dict[str, Any], list[Finding]]:
    """Load and structurally validate a verification document.

    Returns ``(document, findings)``. Raises :class:`CdvError` for conditions
    that make a verdict impossible to compute.
    """
    require_yaml()

    if not os.path.isfile(path):
        raise CdvError(
            RULE_INPUT_MISSING,
            f"no verification document found at '{path}'",
            path=os.path.abspath(path),
        )

    try:
        with open(path, "r", encoding="utf-8") as handle:
            raw = handle.read()
    except OSError as exc:
        raise CdvError(
            RULE_INPUT_MISSING, f"cannot read '{path}': {exc}", path=path
        ) from exc

    try:
        document = yaml.safe_load(raw)
    except yaml.YAMLError as exc:  # type: ignore[union-attr]
        raise CdvError(
            RULE_YAML_MALFORMED,
            f"'{path}' is not valid YAML: {exc}",
            path=path,
        ) from exc

    if document is None:
        raise CdvError(
            RULE_ROOT_NOT_MAPPING,
            f"'{path}' is empty; expected a YAML mapping",
            path=path,
        )

    if not isinstance(document, dict):
        raise CdvError(
            RULE_ROOT_NOT_MAPPING,
            f"'{path}' root must be a mapping, found {type(document).__name__}",
            path=path,
        )

    findings: list[Finding] = []

    # --- version -----------------------------------------------------------
    if "version" not in document:
        raise CdvError(
            RULE_SCHEMA_MISSING_VERSION,
            f"'{path}' has no 'version' key; add `version: {SCHEMA_VERSION}`",
            path=path,
        )

    version = document.get("version")
    if not isinstance(version, int) or isinstance(version, bool):
        raise CdvError(
            RULE_SCHEMA_UNSUPPORTED,
            f"'version' must be an integer, found {version!r}",
            found=version,
        )
    if version != SCHEMA_VERSION:
        raise CdvError(
            RULE_SCHEMA_UNSUPPORTED,
            f"schema version {version} is not supported by this engine "
            f"(supported: {SCHEMA_VERSION}); run the migration documented in "
            "INSTALL.md",
            found=version,
            supported=[SCHEMA_VERSION],
        )

    # --- required keys -----------------------------------------------------
    for key in REQUIRED_TOP_LEVEL_KEYS:
        if key not in document:
            raise CdvError(
                RULE_MISSING_REQUIRED_KEY,
                f"'{path}' is missing required top-level key '{key}'",
                key=key,
            )

    if not isinstance(document.get("claims"), list):
        raise CdvError(
            RULE_MISSING_REQUIRED_KEY,
            "'claims' must be a list",
            key="claims",
        )

    # --- unknown keys ------------------------------------------------------
    # Unknown top-level keys are an ERROR, not a warning. A typo such as
    # `claim:` instead of `claims:` or `oracle:` instead of `oracles:` would
    # otherwise be silently ignored, and the resulting document would pass with
    # its constraints never evaluated.
    for key in document:
        if key not in KNOWN_TOP_LEVEL_KEYS:
            findings.append(
                Finding(
                    rule=RULE_UNKNOWN_TOP_LEVEL_KEY,
                    severity=ERROR,
                    message=(
                        f"unknown top-level key '{key}'; expected one of "
                        f"{sorted(KNOWN_TOP_LEVEL_KEYS)}"
                    ),
                    subject=str(key),
                )
            )

    for key in ("oracles",):
        if key in document and not isinstance(document[key], dict):
            findings.append(
                Finding(
                    rule=RULE_MISSING_REQUIRED_KEY,
                    severity=ERROR,
                    message=f"'{key}' must be a mapping of oracle-id to definition",
                    subject=key,
                )
            )

    for key in ("conflicts", "independence_exceptions", "criticality_exceptions"):
        if key in document and not isinstance(document[key], list):
            findings.append(
                Finding(
                    rule=RULE_MISSING_REQUIRED_KEY,
                    severity=ERROR,
                    message=f"'{key}' must be a list",
                    subject=key,
                )
            )

    return document, findings


def find_document(start: str) -> str | None:
    """Locate verification.yaml from ``start`` upward.

    Adapters use this so that the guard works from a subdirectory of the
    project as well as from its root.
    """
    current = os.path.abspath(start)
    if os.path.isfile(current):
        current = os.path.dirname(current)
    while True:
        candidate = os.path.join(current, DEFAULT_FILENAME)
        if os.path.isfile(candidate):
            return candidate
        parent = os.path.dirname(current)
        if parent == current:
            return None
        current = parent
