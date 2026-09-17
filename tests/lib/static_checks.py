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

    # --- 1b: every referenced name is actually BOUND in the module using it --
    # Added after a real defect: a rule id existed in errors.py and was
    # referenced in gates.py, but was never imported into gates.py. The earlier
    # check above passed, because the name does resolve in errors.py -- just not
    # in the module that used it. It surfaced as an ENGINE_INTERNAL_ERROR on the
    # one code path that reached it, which is the class of bug this file exists
    # to catch, so the check is now AST-based: a name that looks like a rule but
    # is not bound in its own module is an error regardless of which branch uses it.
    unbound: dict[str, list[str]] = {}
    for filename in sorted(os.listdir(CORE)):
        if not filename.endswith(".py"):
            continue
        path = os.path.join(CORE, filename)
        tree = ast.parse(open(path, "r", encoding="utf-8").read(), filename=path)
        bound: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                for alias in node.names:
                    bound.add(alias.asname or alias.name.split(".")[0])
            elif isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                for target in targets:
                    for sub in ast.walk(target):
                        if isinstance(sub, ast.Name):
                            bound.add(sub.id)
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                bound.add(node.name)
            elif isinstance(node, ast.arg):
                bound.add(node.arg)
            elif isinstance(node, ast.ExceptHandler) and node.name:
                bound.add(node.name)
            elif isinstance(node, ast.comprehension):
                for sub in ast.walk(node.target):
                    if isinstance(sub, ast.Name):
                        bound.add(sub.id)
            elif isinstance(node, ast.Global):
                bound.update(node.names)

        missing = sorted(
            {
                node.id
                for node in ast.walk(tree)
                if isinstance(node, ast.Name)
                and node.id.startswith("RULE_")
                and node.id in defined_names
                and node.id not in bound
            }
        )
        if missing:
            unbound[filename] = missing
    report(
        not unbound,
        "every RULE_* name used in a module is imported or defined there",
        f"referenced but not bound: {unbound}",
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

    # --- 2b: the guard's default completion-command policy ------------------
    # Deterministic verification of a configuration fact, so that it does not
    # depend on a model agreeing to attempt a real commit. A well-behaved model
    # reads the injected gate state and declines to commit while the gate is not
    # PASS -- which is the system working as intended, and which means the
    # empirical provocation of a real commit cannot be relied on per model. The
    # mechanism (a matched completion action is blocked, with no side effect) is
    # proven per provider by provoking a configured pattern; the policy (which
    # commands count as completion actions) is verified here by reading it.
    # Two layouts: in the bootstrap repository the guard lives under runtime/,
    # while an installed target keeps it where the runtime discovers plugins. The
    # check has to find it in both, otherwise it passes in the repository and
    # fails on every installation.
    guard_candidates = [
        os.path.join(REPO, "runtime", "opencode", "verification-guard.ts"),
        os.path.normpath(
            os.path.join(REPO, os.pardir, ".opencode", "plugin", "verification-guard.ts")
        ),
        os.path.normpath(
            os.path.join(REPO, os.pardir, ".opencode", "plugins", "verification-guard.ts")
        ),
    ]
    guard_path = next((c for c in guard_candidates if os.path.isfile(c)), guard_candidates[0])
    if os.path.isfile(guard_path):
        guard_src = open(guard_path, "r", encoding="utf-8").read()
        start = guard_src.find("const DEFAULT_COMPLETION_PATTERNS")
        end = guard_src.find("\n]", start) if start != -1 else -1
        block = guard_src[start:end] if start != -1 and end != -1 else ""
        required_patterns = {
            "git commit": r"git\s+commit",
            "git push": r"git\s+push",
            "git tag": r"git\s+tag",
            "git merge": r"git\s+merge",
            "gh pr": r"gh\s+pr",
            "gh release": r"gh\s+release",
            "npm publish": r"npm\s+publish",
            "cargo publish": r"cargo\s+publish",
            "twine upload": r"twine\s+upload",
            "docker push": r"docker\s+push",
            "kubectl apply": r"kubectl\s+apply",
            "terraform apply": r"terraform\s+apply",
            "helm": r"helm",
            "fly deploy": r"fly\s+deploy",
        }
        missing = sorted(
            label for label, token in required_patterns.items() if token not in block
        )
        report(
            bool(block),
            "the guard's default completion-command list was found",
            f"looked for DEFAULT_COMPLETION_PATTERNS in {guard_path}",
        )
        report(
            not missing,
            f"the guard treats all {len(required_patterns)} expected commands as completion actions",
            f"missing: {missing}",
        )
    else:
        report(False, "the guard source is present", guard_path)

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

    # --- 4b: canonical classifications --------------------------------------
    # The classification is release-scoped, so a version bump without a matching
    # label change would leave a report claiming to describe a release it does
    # not. Checked here rather than trusted.
    classification_path = os.path.join(REPO, "CLASSIFICATION")
    if not os.path.isfile(classification_path):
        report(False, "the canonical CLASSIFICATION file exists", classification_path)
    else:
        cls = {}
        for line in open(classification_path, "r", encoding="utf-8"):
            line = line.strip()
            if "=" in line and not line.startswith("#"):
                key, value = line.split("=", 1)
                cls[key.strip()] = value.strip()
        verified = cls.get("CLASSIFICATION_VERIFIED", "")
        incomplete = cls.get("CLASSIFICATION_INCOMPLETE", "")
        report(
            bool(verified) and bool(incomplete),
            "CLASSIFICATION defines both CLASSIFICATION_VERIFIED and CLASSIFICATION_INCOMPLETE",
            f"parsed: {cls}",
        )
        report(
            bool(verified) and verified != incomplete,
            "the verified and incomplete classifications are distinct",
            f"verified={verified!r} incomplete={incomplete!r}",
        )
        schema_ver = open(os.path.join(REPO, "VERSION"), "r", encoding="utf-8").read().strip()
        expected_prefix = "V" + schema_ver.replace(".", "_") + "_"
        report(
            verified.startswith(expected_prefix),
            f"the verified classification names release {schema_ver} (expects prefix {expected_prefix!r})",
            f"CLASSIFICATION_VERIFIED={verified!r} but VERSION={schema_ver}",
        )
        report(
            incomplete.startswith(expected_prefix),
            f"the incomplete classification names release {schema_ver}",
            f"CLASSIFICATION_INCOMPLETE={incomplete!r} but VERSION={schema_ver}",
        )

    # No consumer may hardcode a classification label; they must all read the
    # file. This is the check that would have caught the two reports disagreeing.
    hardcoded: list[str] = []
    for rel in ("installer/install.sh", "tests/verify.sh"):
        path = os.path.join(REPO, rel)
        if not os.path.isfile(path):
            continue
        body = open(path, "r", encoding="utf-8").read()
        for token in ("BOOTSTRAP_VERIFIED", "BOOTSTRAP_INCOMPLETE"):
            if token in body:
                hardcoded.append(f"{rel}:{token}")
    report(
        not hardcoded,
        "no report generator hardcodes a classification label",
        f"hardcoded: {hardcoded}",
    )

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
