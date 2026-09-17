#!/usr/bin/env python3
"""Independent readback of an installation.

The installer saying "installation successful" is not evidence. This script
re-derives the installation's integrity from first principles:

  1. every file recorded in the installation manifest is re-hashed and compared
     with the hash recorded at install time (detects post-install drift)
  2. every hash recorded in the manifest is checked against the *bootstrap
     source* where the file has a source counterpart, so the manifest cannot
     vouch for a file the bootstrap never shipped
  3. the guard is byte-identical to the guard in the bootstrap source
  4. the guard sits directly in the plugin directory, because OpenCode
     discovers `{plugin,plugins}/*.{ts,js}` and a nested file would never load
  5. the engine reports its own version from a separate process, and that version
     matches the VERSION file on disk
  6. the engine enumerates the same rule ids as the bootstrap source, read
     statically from the source file rather than by importing it

None of these checks consults the installer's output. Steps 5 and 6 observe the
installed engine from outside, through its public CLI, in a fresh process.

Exit code 0 only when every check holds.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys

GREEN = "\033[32m"
RED = "\033[31m"
RESET = "\033[0m"

failures: list[str] = []


def check(ok: bool, label: str, detail: str = "") -> bool:
    mark = f"{GREEN}ok{RESET}" if ok else f"{RED}FAIL{RESET}"
    print(f"  {mark}   {label}" + (f"\n         {detail}" if detail and not ok else ""))
    if not ok:
        failures.append(label)
    return ok


def sha256(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


def source_rules(source: str) -> set[str]:
    """Read rule id *values* from the bootstrap source, without importing it.

    Parsed textually so that the comparison is against the shipped source file
    rather than against whatever happens to be importable in this interpreter.
    """
    path = os.path.join(source, "core", "cdv", "errors.py")
    body = open(path, "r", encoding="utf-8").read()
    values = set()
    for match in re.finditer(r'^RULE_[A-Z0-9_]+\s*=\s*"([^"]+)"', body, re.MULTILINE):
        values.add(match.group(1))
    return values


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", required=True)
    parser.add_argument("--source", required=True)
    parser.add_argument("--expect-version", required=True)
    args = parser.parse_args()

    project = os.path.abspath(args.project)
    source = os.path.abspath(args.source)
    engine = os.path.join(project, ".verification")
    manifest_path = os.path.join(engine, "manifest.json")

    print("=" * 78)
    print("INDEPENDENT READBACK")
    print("=" * 78)
    print(f"project : {project}")
    print(f"source  : {source}")
    print("-" * 78)

    # --- 0. the manifest exists -------------------------------------------
    if not check(os.path.isfile(manifest_path), "installation manifest exists", manifest_path):
        print(f"{RED}INDEPENDENT_READBACK=FAIL{RESET}")
        return 1

    manifest = json.load(open(manifest_path, "r", encoding="utf-8"))
    engine_name = os.path.basename(engine)

    # --- 1. recorded hashes match what is on disk -------------------------
    mismatched: list[str] = []
    missing: list[str] = []
    for rel, recorded in manifest.get("files", {}).items():
        path = os.path.join(engine, rel)
        if not os.path.isfile(path):
            missing.append(rel)
            continue
        if sha256(path) != recorded:
            mismatched.append(rel)
    check(not missing, f"all {len(manifest.get('files', {}))} manifested files are present",
          f"missing: {missing}")
    check(not mismatched, "no manifested file has changed since installation",
          f"drifted: {mismatched}")

    # --- 2. every installed file is corroborated by the bootstrap source ----
    #
    # Derived from the manifest rather than hard-coded. An earlier version kept a
    # literal list of installed-to-source pairs, which meant that changing what
    # the installer ships silently broke this check in the other direction: when
    # the source-tree-only edge-case suite stopped being installed, the literal
    # list still demanded it and reported drift for a file that was deliberately
    # no longer there. Deriving the set from the manifest means the install
    # surface can change without this check needing to be edited, and a file that
    # is installed but cannot be corroborated is reported rather than missed.
    # Manifest keys are relative to the engine directory, so the mapping is too.
    INSTALLED_TO_SOURCE = {
        "core/cdv/": "core/cdv/",
        "core/templates/": "templates/",
        "schema/": "schema/",
        "tests/": "tests/",
        "bin/cdv": "bin/cdv",
        "VERSION": "VERSION",
        "CLASSIFICATION": "CLASSIFICATION",
    }
    # Written or generated by the installer, so they have no source counterpart.
    GENERATED = {"bin/cdv", "manifest.json", "guard.config.json", ".gitignore"}

    def source_counterpart(installed_relative: str) -> str | None:
        for prefix, source_prefix in INSTALLED_TO_SOURCE.items():
            if installed_relative == prefix:
                return source_prefix
            if prefix.endswith("/") and installed_relative.startswith(prefix):
                return source_prefix + installed_relative[len(prefix):]
        return None

    uncorroborated: list[str] = []
    not_comparable: list[str] = []
    for relative in manifest.get("files", {}):
        if relative in GENERATED:
            continue
        counterpart = source_counterpart(relative)
        if counterpart is None:
            uncorroborated.append(relative)
            continue
        installed_path = os.path.join(engine, relative)
        source_path = os.path.join(source, counterpart)
        if not os.path.isfile(installed_path):
            # Absent from the install. Whether that is correct is the packaging
            # contract's business, not this check's; asserting it here is how the
            # two checks came to disagree.
            continue
        if not os.path.isfile(source_path):
            uncorroborated.append(f"{relative} (no source file at {counterpart})")
            continue
        if sha256(installed_path) != sha256(source_path):
            not_comparable.append(relative)

    check(
        not not_comparable,
        "installed engine, schema and test suite are byte-identical to the bootstrap source",
        f"differ: {not_comparable}",
    )
    check(
        not uncorroborated,
        "every installed file can be corroborated by the bootstrap source",
        f"unexplained installed file(s): {uncorroborated}",
    )


    # --- 3. the guard is the reviewed guard ------------------------------
    guard_rel = manifest.get("guard_file", os.path.join(".opencode", "plugin", "verification-guard.ts"))
    guard_installed = os.path.join(project, guard_rel)
    guard_source = os.path.join(source, "runtime", "opencode", "verification-guard.ts")
    if check(os.path.isfile(guard_installed), "guard installed", guard_installed):
        recorded_guard = manifest.get("guard_installed_sha256")
        if recorded_guard:
            check(sha256(guard_installed) == recorded_guard,
                  "installed guard still hashes to the value recorded at install time",
                  f"{sha256(guard_installed)} != {recorded_guard}")
        if os.path.isfile(guard_source):
            check(sha256(guard_installed) == sha256(guard_source),
                  "installed guard is byte-identical to the bootstrap source guard",
                  f"{sha256(guard_installed)} != {sha256(guard_source)}")

    # --- 4. discovery contract -------------------------------------------
    plugin_dir = os.path.join(project, ".opencode", "plugin")
    plugins_dir = os.path.join(project, ".opencode", "plugins")
    guard_direct = os.path.join(plugin_dir, "verification-guard.ts")
    guard_direct_alt = os.path.join(plugins_dir, "verification-guard.ts")
    check(os.path.isfile(guard_direct) or os.path.isfile(guard_direct_alt),
          "guard sits directly in .opencode/plugin/ or .opencode/plugins/ "
          "(OpenCode discovers {plugin,plugins}/*.{ts,js} and ignores nested files)",
          f"looked in {plugin_dir} and {plugins_dir}")

    # --- 5. the engine reports the version the files claim ---------------
    installed_version = None
    version_file = os.path.join(engine, "VERSION")
    if check(os.path.isfile(version_file), "VERSION file present", version_file):
        installed_version = open(version_file, "r", encoding="utf-8").read().strip()
        check(installed_version == args.expect_version,
              f"VERSION file is {args.expect_version}",
              f"found {installed_version!r}")

    launcher = os.path.join(engine, "bin", "cdv")
    if check(os.path.isfile(launcher), "engine launcher present", launcher):
        check(os.access(launcher, os.X_OK), "engine launcher is executable", launcher)

    engine_version = None
    try:
        proc = subprocess.run(
            [sys.executable, launcher, "version", "--json"],
            capture_output=True, text=True, timeout=120, cwd=project,
        )
        payload = json.loads(proc.stdout)
        engine_version = payload.get("engine")
        check(True, f"engine executes in a separate process and reports version {engine_version}")
    except Exception as exc:  # noqa: BLE001
        check(False, "engine executes in a separate process", f"{type(exc).__name__}: {exc}")

    if engine_version and installed_version:
        check(engine_version == installed_version,
              "engine-reported version matches the VERSION file on disk",
              f"{engine_version} != {installed_version}")

    # --- 6. the installed engine enumerates the bootstrap's rules ---------
    try:
        proc = subprocess.run(
            [sys.executable, launcher, "rules", "--json"],
            capture_output=True, text=True, timeout=120, cwd=project,
        )
        installed_rules = set(json.loads(proc.stdout))
    except Exception as exc:  # noqa: BLE001
        installed_rules = set()
        check(False, "engine enumerates its rule ids", f"{type(exc).__name__}: {exc}")

    expected_rules = source_rules(source)
    if installed_rules or expected_rules:
        missing_rules = sorted(expected_rules - installed_rules)
        extra_rules = sorted(installed_rules - expected_rules)
        check(not missing_rules and not extra_rules,
              f"installed engine emits exactly the bootstrap's {len(expected_rules)} rule ids",
              f"missing: {missing_rules}; extra: {extra_rules}")

    print("-" * 78)
    if failures:
        print(f"{RED}INDEPENDENT_READBACK=FAIL{RESET} ({len(failures)} check(s) failed)")
        for label in failures:
            print(f"  - {label}")
        return 1
    print(f"{GREEN}INDEPENDENT_READBACK=PASS{RESET}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
