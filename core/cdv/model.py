"""Claim model: the vocabulary and conservative defaults.

This module is the single place where the meaning of "critical" is defined.
Adapters must import these constants rather than restate them.
"""

from __future__ import annotations

# --- Claim types -----------------------------------------------------------
CLAIM_TYPES = (
    "STATE",       # the system is in a particular state
    "BEHAVIOR",    # the system does a particular thing
    "ARTIFACT",    # a durable artifact is produced
    "TEMPORAL",    # the claim holds across time / restart / recovery
    "OUTCOME",     # the world or the user's task actually changed
)

# --- Criticality -----------------------------------------------------------
CRITICAL = "CRITICAL"
NON_CRITICAL = "NON_CRITICAL"
CRITICALITY_VALUES = (CRITICAL, NON_CRITICAL)

# --- Evidence result vocabulary -------------------------------------------
RESULT_PASS = "PASS"
RESULT_FAIL = "FAIL"
RESULT_BLOCKED = "BLOCKED"
RESULT_NOT_RUN = "NOT_RUN"
RESULT_VALUES = (RESULT_PASS, RESULT_FAIL, RESULT_BLOCKED, RESULT_NOT_RUN)

# --- Oracle kinds ----------------------------------------------------------
ORACLE_KINDS = (
    "internal",              # inside the producing system
    "independent-technical",  # a different technical mechanism
    "cross-modal",           # a different sensory/observational modality
    "external-reality",      # the world outside the software
)

# Independent-qualification outcome vocabulary.
QUAL_PASS = "PASS"        # positive case behaved as a positive case should
QUAL_DETECTED = "DETECTED"  # negative case was correctly rejected
QUAL_NOT_DETECTED = "NOT_DETECTED"
QUAL_NOT_RUN = "NOT_RUN"

# --- Oracle qualification assurance ---------------------------------------
# Two modes, deliberately not interchangeable.
#
#   EXECUTED  the engine ran the oracle against a known-good and a known-bad
#             case and observed the results. The verifier-of-the-verifier claim
#             is backed by execution.
#   DECLARED  the project asserts that both cases were run. Auditable, and much
#             better than nothing, but it is an assertion about the verifier made
#             by the same party that benefits from the verifier passing.
#
# The distinction is preserved in every output. A DECLARED oracle must never
# silently appear equivalent to an EXECUTED one.
QUALIFICATION_EXECUTED = "EXECUTED"
QUALIFICATION_DECLARED = "DECLARED"
QUALIFICATION_MODES = (QUALIFICATION_EXECUTED, QUALIFICATION_DECLARED)

# Accepted spellings of the executed mode. `executable` was the v1.0.0 name and
# remains valid so existing documents keep working; `executed` is canonical
# because it describes what happened rather than what was possible.
QUALIFICATION_MODE_ALIASES = {
    "executed": QUALIFICATION_EXECUTED,
    "executable": QUALIFICATION_EXECUTED,
    "declared": QUALIFICATION_DECLARED,
}

# Claim-level assurance summary.
ASSURANCE_EXECUTED = "EXECUTED"
ASSURANCE_DECLARED = "DECLARED"
ASSURANCE_MIXED = "MIXED"
ASSURANCE_NONE = "NONE"

# A DECLARED qualification must state why it is declared and why executable
# qualification is unavailable. Both are assertions, so a short string is not
# accepted: the reasoning has to be written down to be reviewable.
MIN_DECLARED_RATIONALE_LENGTH = 40

# --- Criticality defaults --------------------------------------------------
# A claim matching any trigger token is critical by default. Matching is
# case-insensitive substring matching, which errs toward classification as
# critical (the conservative direction), but the tokens are chosen so that they
# cannot fire from inside an unrelated common word: bare fragments like "ui"
# (matches "build"), "tax" (matches "syntax"), "token" (matches "tokenizer") or
# "life" (matches "lifecycle") are deliberately absent. A trigger table that
# fires on everything is not conservative, it is unusable, and an unusable gate
# is one that gets switched off.
CRITICALITY_TRIGGERS: dict[str, tuple[str, ...]] = {
    "money": (
        "payment", "price", "pricing", "invoice", "billing", "refund",
        "currency", "monetary", "financial", "balance owed", "salary",
        "budget", "monetary amount", "checkout total", "tax rate", "vat ",
        "transaction amount", "charge the customer",
    ),
    "data-integrity": (
        "data integrity", "corrupt", "integrity check", "checksum",
        "schema migration", "database migration", "backfill", "deduplicat",
        "truncat", "overwrite", "data loss", "reconcil", "unique constraint",
        "foreign key", "rolling back", "rollback",
    ),
    "privacy": (
        "privacy", "personal data", "personally identifiable", "pii",
        "gdpr", "consent", "anonymis", "anonymiz", "redact", "data subject",
        "right to be forgotten", "tracking pixel", "user's location",
    ),
    "security": (
        "security", "vulnerab", "injection", "sanitiz", "sanitis",
        "exploit", "cve-", "secrets", "credential", "private key",
        "certificate", "encrypt", "decrypt", "signature verification",
        "privilege escalation", "least privilege", "access control list",
    ),
    "authentication": (
        "authentication", "authorization", "authenticate", "login",
        "log in", "sign in", "logout", "password", "mfa", "2fa", "oauth",
        "identity provider", "single sign-on", "session token", "user role",
        "permission check",
    ),
    "persistent-state": (
        "persist", "database", "durable storage", "stored in", "survives",
        "on disk", "committed to", "rehydrat", "cache invalidat",
        "write-ahead", "after restart", "after a restart", "state file",
    ),
    "external-side-effect": (
        "external service", "third party", "third-party", "webhook",
        "http request", "outbound request", "publish to", "deploy",
        "provision", "notification is sent", "email is sent", "sms",
        "push notification", "upload", "remote server", "side effect",
    ),
    "generated-artifact": (
        "artifact", "export", "pdf", "docx", "xlsx", "csv file", "report file",
        "printer", "print job", "printable", "printed page", "print to",
        "render", "downloadable", "attachment", "generated file",
        "output file", "image file",
    ),
    "release-readiness": (
        "release", "changelog", "publish to production", "production",
        "rollout", "distribution", "release artifact", "ship to", "semver",
        "versioned release",
    ),
    "user-visible-correctness": (
        "user sees", "user-visible", "user facing", "user-facing",
        "displayed to", "shown to the user", "visible to the user",
        "on screen", "user interface", "what the user reads",
    ),
    "safety": (
        "safety", "hazard", "injur", "danger", "emergency", "medical",
        "physical device", "hardware", "actuator", "motor", "fire risk",
        "electrical shock",
    ),
    "destructive-or-irreversible": (
        "destructive", "irreversible", "cannot be undone", "cannot be recovered",
        "drop table", "purge", "wipe", "factory reset", "permanently delete",
        "truncate table", "overwrites existing",
    ),
}

# A NON_CRITICAL classification must state a rationale at least this long, in
# addition to any trigger-specific requirements.
MIN_RATIONALE_LENGTH = 80


def match_criticality_triggers(*texts: object) -> dict[str, list[str]]:
    """Return {area: [matched tokens]} for any criticality trigger present.

    Matching is case-insensitive substring matching over all supplied texts.
    Substring matching is intentional: it errs toward classification as
    critical, which is the conservative direction.
    """
    haystack = " \n ".join(
        str(t).lower() for t in texts if t is not None
    )
    matched: dict[str, list[str]] = {}
    for area, tokens in CRITICALITY_TRIGGERS.items():
        hits = [tok for tok in tokens if tok in haystack]
        if hits:
            matched[area] = hits
    return matched
