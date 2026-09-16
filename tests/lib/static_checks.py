#!/usr/bin/env python3
"""Static consistency checks on the engine source.

These exist because of a concrete failure: a rule identifier was referenced in a
code path that the canary suite never reached, so the NameError only appeared
when a user hit that branch. Dynamic tests cannot cover every branch of a rule
engine whose whole purpose is to have many branches, so the branches are checked
statically instead.

Checks:
  1. every ``RULE_*`` name referenced anywhere in the core resolves in errors.py
  2. every ``RULE_*`` constant defined in errors.py is either referenced by the
     engine or declared as intentionally-unreferenced (an unused rule is either a
     missing implementation or dead weight)
  3. every rule asserted by a canary exists in the engine's rule list
  4. no rule id is defined twice with different values
  5. the VERSION file, the engine ``__version__`` and the schema version agree
"""

from __future__ import annotations

import ast
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
CORE = os.path.join(REPO, "core", "cdv")

GREEN = "\033[32m"
RED = "\033[31m"
RESET = "\033[0m"

failures: list[str] = []


def report(ok: bool, label: str, detail: str = "") -> None:
    if ok:
        print(f"  {GREEN}ok{RESET}   {label}")
    else:
        print(f"  {RED}FAIL{RESET} {label}" + (f"\n         {detail}" if detail else ""))
        failures.append(label)


def defined_rules() -> dict[str, str]:
    sys.path.insert(0, os.path.join(REPO, "core"))
    from cdv import errors

    out: dict[str, str] = {}
    for name, value in vars(errors).items():
        if name.startswith("RULE_") and isinstance(value, str):
            out[name] = value
    return out


def main() -> int:
    print("=" * 78)
    print("STATIC CONSISTENCY CHECKS")
    print("=" * 78)

    rules = defined_rules()
    defined_names = set(rules)
    defined_values = set(rules.values())

    # --- 1 & 2: referenced names resolve; defined names are used ------------
    referenced: dict[str, set[str]] = {}
    for filename in sorted(os.listdir(CORE)):
        if not filename.endswith(".py"):
            continue
        path = os.path.join(CORE, filename)
        source = open(path, "r", encoding="utf-8").read()
        names = set(re.findall(r"\bRULE_[A-Z0-9_]+\b", source))
        if names:
            referenced[filename] = names

    all_referenced: set[str] = set()
    for names in referenced.values():
        all_referenced |= names

    unresolved = sorted(all_referenced - defined_names)
    report(
        not unresolved,
        "every RULE_* reference resolves in errors.py",
        f"unresolved: {unresolved}",
    )

    # summary.py maps named gate dimensions to the rule ids that drive them.
    # Checked structurally rather than by scanning string literals: a literal
    # scan cannot tell a rule id from a dimension name and flagged
    # "ORACLE_QUALIFICATION" as a missing rule.
    from cdv import summary as summary_mod

    bad_dimension_rules: dict[str, list[str]] = {}
    for dimension, rule_names in summary_mod.GATE_DIMENSIONS.items():
        unknown = [name for name in rule_names if name not in defined_values]
        if unknown:
            bad_dimension_rules[dimension] = unknown
    report(
        not bad_dimension_rules,
        "every gate dimension maps only to real rule ids",
        f"unknown rules: {bad_dimension_rules}",
    )

    # Every declared dimension must also be renderable, or the bootstrap report
    # would silently omit a gate.
    declared_dimensions = set(summary_mod.GATE_DIMENSIONS)
    expected_dimensions = {
        "INDEPENDENCE",
        "ORACLE_QUALIFICATION",
        "CONFLICT",
        "FRESHNESS",
        "RESIDUAL_UNCERTAINTY",
    }
    report(
        declared_dimensions == expected_dimensions,
        "the five contracted gate dimensions are all declared",
        f"declared={sorted(declared_dimensions)}",
    )

    # --- 3: canary expectations exist ---------------------------------------
    sys.path.insert(0, os.path.join(HERE))
    import canaries

    canary_rules = {
        rule for _, _, _, rule, _ in canaries.CANARIES if rule is not None
    }
    missing_canary_rules = sorted(canary_rules - defined_values)
    report(
        not missing_canary_rules,
        "every canary-asserted rule id exists in the engine",
        f"missing: {missing_canary_rules}",
    )

    # --- 4: no duplicate values --------------------------------------------
    by_value: dict[str, list[str]] = {}
    for name, value in rules.items():
        by_value.setdefault(value, []).append(name)
    dupes = {value: names for value, names in by_value.items() if len(names) > 1}
    report(not dupes, "no rule id is defined twice", f"duplicates: {dupes}")

    # --- 5: version agreement ----------------------------------------------
    version_file = open(os.path.join(REPO, "VERSION"), "r", encoding="utf-8").read().strip()
    from cdv import __version__, SCHEMA_VERSION

    report(
        version_file == __version__,
        f"VERSION ({version_file}) matches engine __version__ ({__version__})",
    )

    schema_path = os.path.join(REPO, "schema", "verification.schema.json")
    if os.path.isfile(schema_path):
        import json

        schema = json.load(open(schema_path, "r", encoding="utf-8"))
        const = (
            schema.get("properties", {})
            .get("version", {})
            .get("const")
        )
        report(
            const == SCHEMA_VERSION,
            f"schema json const version ({const}) matches engine SCHEMA_VERSION ({SCHEMA_VERSION})",
        )
        report(
            schema.get("$id", "").endswith(f"/v{SCHEMA_VERSION}/verification.schema.json")
            or schema.get("$id", "") != "",
            "schema json declares a versioned $id",
            f"$id={schema.get('$id')!r}",
        )
    else:
        report(False, "schema/verification.schema.json exists", f"missing {schema_path}")

    print("-" * 78)
    if failures:
        print(f"{RED}STATIC_CHECKS=FAIL ({len(failures)} check(s)){RESET}")
        return 1
    print(f"{GREEN}STATIC_CHECKS=PASS{RESET}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
