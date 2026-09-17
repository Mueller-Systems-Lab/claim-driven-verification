#!/usr/bin/env python3
"""Static reliability checks for the repository's shell entry points.

Why this exists
---------------
`tests/verify.sh` and `installer/install.sh` run under `set -u`, where an
undefined or out-of-order variable aborts the run part-way through. That class
produced three real defects during development -- `VERBOSE`, `SECOND_MODEL` and
`SECOND_OK` -- each of which passed `bash -n`, because `bash -n` parses without
evaluating and therefore cannot see either problem.

ShellCheck is used as the primary tool, because it is the right tool. But it is
used with a measured understanding of what it does and does not catch. Measured
on ShellCheck 0.11.0:

    script with a variable referenced and never assigned anywhere
        -> SC2154, ONLY with --enable=all; silent at every default severity
    script with a variable used before it is assigned later in the file
        -> NOT REPORTED AT ALL, at any severity, with --enable=all

So ShellCheck covers the unbound case and does not cover the ordering case. The
ordering case is `SECOND_OK`, the defect that actually shipped into a run. A gate
that relied on ShellCheck alone would therefore have missed one of the three
demonstrated defects and reported PASS, which is the failure mode this whole
project exists to reject.

Hence: ShellCheck for what it can see, plus a small deterministic scan for the
ordering class it cannot.

Findings are triaged. Only codes that can cause a wrong or aborted run fail the
gate; the style notes that `--enable=all` emits by the hundred (SC2250 "prefer
braces", SC2292 "prefer [[ ]]") are counted and reported but never fail it,
because mechanically rewriting harmless style is not the job.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))

GREEN = "\033[32m"
RED = "\033[31m"
YELLOW = "\033[33m"
RESET = "\033[0m"

# Codes that can cause an undefined variable, an aborted run, a silently wrong
# value or an unhandled failure. Each is here for a stated reason, not because
# ShellCheck emitted it.
CORRECTNESS_CODES = {
    "SC2154": "variable referenced but never assigned anywhere (the unbound class)",
    "SC2034": "variable assigned but never used (dead residue of the same class)",
    "SC2329": "function defined but never invoked (dead code)",
    "SC2086": "unquoted expansion, splits on whitespace and globs",
    "SC2046": "unquoted command substitution",
    "SC2164": "cd without a guard, so a failure continues in the wrong directory",
    "SC2181": "indirect check of $? instead of testing the command",
    "SC2128": "expanding an array without an index",
    "SC2145": "mixing string and array in expansion",
    "SC2115": "rm with a possibly-empty variable",
    "SC2068": "unquoted array expansion",
}

# Variables the shell or the environment provides. A reference to one of these is
# not an unbound-variable defect; flagging them is how a check earns a reputation
# for false alarms and then gets switched off.
SHELL_PROVIDED = {
    "PATH", "HOME", "PWD", "OLDPWD", "SHELL", "USER", "LOGNAME", "TERM", "TMPDIR",
    "LANG", "LANGUAGE", "HOSTNAME", "HOSTTYPE", "OSTYPE", "MACHTYPE", "IFS",
    "BASH", "BASH_VERSION", "BASH_SOURCE", "BASH_LINENO", "LINENO", "FUNCNAME",
    "RANDOM", "SECONDS", "EPOCHSECONDS", "EPOCHREALTIME", "PPID", "UID", "EUID",
    "GROUPS", "PIPESTATUS", "REPLY", "SHELLOPTS", "BASHOPTS", "PS1", "PS2", "PS3",
    "PS4", "COLUMNS", "LINES", "DISPLAY", "EDITOR", "PAGER", "TZ", "SHLVL",
    "XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_CACHE_HOME", "XDG_STATE_HOME",
    "NO_COLOR", "FORCE_COLOR", "CI", "GITHUB_ACTIONS", "DEBUG", "VERBOSE",
    "PREFIX", "DESTDIR", "SUDO_USER", "TMP", "TEMP",
}
# Prefixes the project reserves for its own environment inputs.
ENV_PREFIXES = ("CDV_", "LC_")

# Assignments: NAME=..., local/export/declare/typeset/readonly NAME=,
# for NAME in, read NAME, printf -v NAME, mapfile -t NAME.
ASSIGN_PATTERNS = [
    re.compile(r"(?:^|[\s;(])(?:local|export|declare|typeset|readonly)\s+(?:-[A-Za-z]+\s+)*([A-Za-z_][A-Za-z0-9_]*)="),
    re.compile(r"(?:^|[\s;(])([A-Za-z_][A-Za-z0-9_]*)="),
    re.compile(r"\bfor\s+([A-Za-z_][A-Za-z0-9_]*)\s+in\b"),
    re.compile(r"\bread\s+(?:-[A-Za-z]+\s+)*([A-Za-z_][A-Za-z0-9_]*)"),
    re.compile(r"printf\s+-v\s+([A-Za-z_][A-Za-z0-9_]*)"),
    re.compile(r"\bmapfile\s+(?:-[A-Za-z]+\s+)*([A-Za-z_][A-Za-z0-9_]*)"),
    re.compile(r"\bearray_name\s*=\s*([A-Za-z_][A-Za-z0-9_]*)"),
]

# A reference, and whether it is defaulted. `${VAR:-x}`, `${VAR:=x}`, `${VAR:+x}`
# and `${VAR:?}` are all bound by construction and must not be flagged: they are
# the idiomatic way this repository reads optional environment input.
USE_PATTERN = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(:?[-=+?][^}]*)?\}|\$([A-Za-z_][A-Za-z0-9_]*)")
ASSOC_KEY_PATTERN = re.compile(r"\b([A-Za-z_][A-Za-z0-9_]*)\[")

FUNCTION_START = re.compile(r"^\s*(?:function\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*\(\)\s*\{")


def _strip_comment(line: str) -> str:
    """Remove a trailing comment, respecting single quotes.

    Deliberately simple: it only needs to be good enough that a commented-out
    line does not look like a live assignment.
    """
    out = []
    in_single = False
    index = 0
    while index < len(line):
        char = line[index]
        if char == "'" and not in_single:
            in_single = True
        elif char == "'" and in_single:
            in_single = False
        elif char == "#" and not in_single:
            previous = line[index - 1] if index else " "
            if previous in " \t;(":
                break
        out.append(char)
        index += 1
    return "".join(out)


def scan_script(path: str) -> list[tuple[int, str, str]]:
    """Return (line, variable, reason) for ordering and unbound findings.

    Only top-level references are checked for ordering. A reference inside a
    function body is not ordered against top-level assignments, because function
    bodies execute when they are called, not where they are written; comparing
    them textually produces false positives on correct code, and a check that
    cries wolf is one that gets disabled.
    """
    with open(path, "r", encoding="utf-8") as handle:
        raw_lines = handle.read().splitlines()

    top_level: list[bool] = []
    depth = 0
    in_function = False
    for line in raw_lines:
        stripped = _strip_comment(line)
        starting_function = bool(FUNCTION_START.match(stripped))
        if starting_function:
            in_function = True
        top_level.append(not in_function and depth == 0)
        depth += stripped.count("{") - stripped.count("}")
        if in_function and depth <= 0:
            in_function = False
            depth = 0

    assignments: dict[str, list[int]] = {}
    uses: list[tuple[int, str, bool]] = []

    for number, line in enumerate(raw_lines, start=1):
        stripped = _strip_comment(line)
        for pattern in ASSIGN_PATTERNS:
            for match in pattern.finditer(stripped):
                assignments.setdefault(match.group(1), []).append(number)
        # `R[key]=value` and `${R[key]}` are associative-array element accesses,
        # so the array name counts as assigned where it appears in that form.
        for match in ASSOC_KEY_PATTERN.finditer(stripped):
            if re.search(re.escape(match.group(0)) + r"\s*=", stripped):
                assignments.setdefault(match.group(1), []).append(number)
        for match in USE_PATTERN.finditer(stripped):
            name = match.group(1) or match.group(3)
            if not name:
                continue
            defaulted = bool(match.group(2))
            uses.append((number, name, defaulted))

    findings: list[tuple[int, str, str]] = []
    for number, name, defaulted in uses:
        if defaulted:
            continue
        if name in SHELL_PROVIDED or name.startswith(ENV_PREFIXES):
            continue
        assigned_at = assignments.get(name)
        if not assigned_at:
            # ShellCheck reports this as SC2154 under --enable=all; reported here
            # as well so the check is self-contained rather than depending on an
            # optional lint flag being present.
            findings.append(
                (number, name, "referenced but never assigned anywhere in the script")
            )
            continue
        first_use = number
        first_assign = min(assigned_at)
        if first_assign > first_use and top_level[first_use - 1]:
            findings.append(
                (
                    first_use,
                    name,
                    f"used at line {first_use} but first assigned at line {first_assign}",
                )
            )
    return findings


def locate_shellcheck() -> str | None:
    """Find ShellCheck: explicit override, then PATH, then the user-local bin."""
    override = os.environ.get("CDV_SHELLCHECK")
    if override and os.path.isfile(override) and os.access(override, os.X_OK):
        return override
    from shutil import which

    found = which("shellcheck")
    if found:
        return found
    candidate = os.path.join(os.path.expanduser("~"), ".local", "bin", "shellcheck")
    if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
        return candidate
    return None


def shell_scripts() -> list[str]:
    scripts = []
    for root, dirs, names in os.walk(REPO):
        dirs[:] = [d for d in dirs if d not in {".git", "node_modules", "__pycache__"}]
        for name in sorted(names):
            if name.endswith(".sh"):
                scripts.append(os.path.join(root, name))
    return sorted(scripts)


def run_shellcheck(binary: str, scripts: list[str]) -> tuple[int, list[tuple[str, int, str, str, str]]]:
    """Run ShellCheck with optional checks enabled. Returns (count, findings).

    ``--enable=all`` is required: SC2154 is an optional check and is silent at
    every default severity, which is precisely the unbound class we care about.
    """
    proc = subprocess.run(
        [binary, "--enable=all", "--format=gcc", *scripts],
        capture_output=True,
        text=True,
        timeout=300,
    )
    findings: list[tuple[str, int, str, str, str]] = []
    for line in proc.stdout.splitlines():
        match = re.match(r"^([^:]+):(\d+):(\d+): (\w+): (.*?) \[(SC\d+)\]$", line)
        if not match:
            continue
        path, number, _, severity, message, code = match.groups()
        findings.append((os.path.basename(path), int(number), severity, code, message))
    return proc.returncode, findings


def main() -> int:
    print("=" * 78)
    print("SHELL RELIABILITY CHECKS")
    print("=" * 78)

    scripts = shell_scripts()
    if not scripts:
        # shellcheck with no file arguments reads stdin, so an empty set must be
        # handled rather than passed through.
        print("no shell scripts found; nothing to check")
        print("SHELLCHECK_GATE=NOT_APPLICABLE")
        return 0
    print(f"scripts: {len(scripts)}")
    for script in scripts:
        print(f"  {os.path.relpath(script, REPO)}")

    failures: list[str] = []

    # --- 1. ordering and unbound scan (the class ShellCheck cannot see) -----
    print("\nordering / unbound scan")
    ordering_findings = 0
    for script in scripts:
        for number, name, reason in scan_script(script):
            ordering_findings += 1
            failures.append(f"{os.path.relpath(script, REPO)}:{number} {name}: {reason}")
            print(f"  {RED}FAIL{RESET} {os.path.relpath(script, REPO)}:{number} "
                  f"{name}: {reason}")
    if not ordering_findings:
        print(f"  {GREEN}ok{RESET}   no use-before-assignment or unbound references")

    # --- 2. ShellCheck ------------------------------------------------------
    print("\nshellcheck")
    binary = locate_shellcheck()
    if binary is None:
        print(f"  {RED}FAIL{RESET} shellcheck is not available")
        print("       Install it, or set CDV_SHELLCHECK to the binary. The gate is")
        print("       reported NOT_AVAILABLE rather than PASS, because a static check")
        print("       that did not run has not verified anything.")
        print("-" * 78)
        print("SHELLCHECK_GATE=NOT_AVAILABLE")
        print("SHELL_RELIABILITY=FAIL")
        return 1

    version = subprocess.run(
        [binary, "--version"], capture_output=True, text=True, timeout=60
    ).stdout.splitlines()
    version_line = version[1].strip() if len(version) > 1 else "unknown"
    print(f"  binary : {binary}")
    print(f"  version: {version_line}")

    _, findings = run_shellcheck(binary, scripts)
    by_code: dict[str, int] = {}
    for _, _, _, code, _ in findings:
        by_code[code] = by_code.get(code, 0) + 1
    correctness = [f for f in findings if f[3] in CORRECTNESS_CODES or f[2] == "error"]
    style_count = len(findings) - len(correctness)

    for name, number, severity, code, message in correctness:
        failures.append(f"{name}:{number} {severity} {code}: {message}")
        print(f"  {RED}FAIL{RESET} {name}:{number} {severity} {code}: {message} "
              f"({CORRECTNESS_CODES.get(code, 'shellcheck error')})")
    if not correctness:
        print(f"  {GREEN}ok{RESET}   no correctness findings")
    if style_count:
        counts = ", ".join(f"{code}x{count}" for code, count in sorted(by_code.items())
                           if code not in CORRECTNESS_CODES)
        print(f"  {YELLOW}note{RESET} {style_count} style-only finding(s) not gated: {counts}")

    # --- 3. negative canaries: prove the detectors actually detect ----------
    print("\nnegative canaries")
    canary_dir = os.path.join(REPO, ".shell-canary")
    os.makedirs(canary_dir, exist_ok=True)
    unbound = os.path.join(canary_dir, "unbound.sh")
    ordering = os.path.join(canary_dir, "ordering.sh")
    with open(unbound, "w", encoding="utf-8") as handle:
        handle.write("#!/usr/bin/env bash\nset -uo pipefail\necho \"$CANARY_NEVER_ASSIGNED\"\n")
    with open(ordering, "w", encoding="utf-8") as handle:
        handle.write(
            "#!/usr/bin/env bash\nset -uo pipefail\n"
            "if [ \"$CANARY_LATER\" = x ]; then echo early; fi\n"
            "CANARY_LATER=assigned\n"
        )
    try:
        unbound_hits = scan_script(unbound)
        ordering_hits = scan_script(ordering)
        ok_unbound = any("never assigned" in reason for _, _, reason in unbound_hits)
        ok_ordering = any("first assigned at line" in reason for _, _, reason in ordering_hits)
        for label, detected in (
            ("unbound variable", ok_unbound),
            ("use before assignment (ordering)", ok_ordering),
        ):
            if detected:
                print(f"  {GREEN}ok{RESET}   {label}: detected (FAIL_AS_DESIGNED)")
            else:
                failures.append(f"negative canary not detected: {label}")
                print(f"  {RED}FAIL{RESET} {label}: NOT detected — the gate cannot see "
                      f"the defect it claims to cover")
    finally:
        for path in (unbound, ordering):
            if os.path.isfile(path):
                os.remove(path)
        try:
            os.rmdir(canary_dir)
        except OSError:
            pass

    canary_ok = not any("negative canary not detected" in f for f in failures)
    print(
        "SHELL_UNBOUND_NEGATIVE_CANARY="
        + ("FAIL_AS_DESIGNED" if canary_ok else "NOT_DETECTED")
    )

    print("-" * 78)
    if failures:
        print(f"SHELLCHECK_GATE=FAIL ({len(failures)} finding(s))")
        for item in failures:
            print(f"  - {item}")
        return 1
    print("SHELLCHECK_GATE=PASS")
    print("SHELL_RELIABILITY=PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
