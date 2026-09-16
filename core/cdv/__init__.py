"""Claim-Driven Verification (CDV) reference core engine.

Runtime-independent. Contains no OpenCode-specific, editor-specific or
agent-specific logic. Adapters (OpenCode plugin, CI, other agent runtimes)
invoke this package through ``cdv`` and must not re-implement its rules.
"""

__all__ = ["__version__"]

# Version of the *engine implementation*. Kept in sync with the repository
# VERSION file by release engineering; asserted by the bootstrap self-test.
__version__ = "1.0.0"

# Version of the verification.yaml contract this engine understands.
SCHEMA_VERSION = 1
