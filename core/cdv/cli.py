"""Command line interface.

This CLI is the only supported entry point into the engine. Adapters (the
OpenCode guard, CI jobs, future runtimes) shell out to it. Keeping a single
entry point is what prevents the rules from being re-implemented, and therefore
diverging, per adapter.

Exit codes
----------
0  GATE=PASS
1  GATE=FAIL  (including any blocking finding)
2  usage / configuration error
3  missing dependency or prerequisite (fail closed)
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from typing import Any

from . import SCHEMA_VERSION, __version__
from .errors import CdvError
from .gates import validate
from .loader import DEFAULT_FILENAME, find_document, load_document
from . import summary as summary_mod

EXIT_PASS = 0
EXIT_FAIL = 1
EXIT_USAGE = 2
EXIT_DEPENDENCY = 3


def _resolve_document(args: argparse.Namespace) -> str:
    """Resolve the document path, searching upward when not given explicitly."""
    if getattr(args, "path", None):
        return os.path.abspath(args.path)
    start = os.path.abspath(getattr(args, "project", None) or os.getcwd())
    found = find_document(start)
    if found is None:
        raise CdvError(
            "INPUT_FILE_MISSING",
            f"no '{DEFAULT_FILENAME}' found at or above '{start}'. This project is "
            "not bootstrapped, or the guard is pointed at the wrong directory.",
            searched_from=start,
        )
    return found


def _load_and_validate(args: argparse.Namespace):
    path = _resolve_document(args)
    project_dir = os.path.abspath(
        getattr(args, "project", None) or os.path.dirname(path) or os.getcwd()
    )
    document, structural = load_document(path)
    result = validate(
        document,
        path=path,
        project_dir=project_dir,
        allow_execute_oracles=bool(getattr(args, "allow_execute_oracles", False)),
        structural_findings=structural,
    )
    return result


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


def cmd_validate(args: argparse.Namespace) -> int:
    result = _load_and_validate(args)
    if args.json:
        print(summary_mod.render_json(result))
    elif args.keyvalues:
        print(summary_mod.render_keyvalues(result))
    elif args.findings:
        print(summary_mod.render_findings_only(result))
    else:
        print(summary_mod.render_text(result, verbose=args.verbose))
    return result.exit_code


def cmd_summary(args: argparse.Namespace) -> int:
    result = _load_and_validate(args)
    if args.json:
        print(summary_mod.render_json(result))
    else:
        print(summary_mod.render_keyvalues(result))
    return result.exit_code


def cmd_findings(args: argparse.Namespace) -> int:
    result = _load_and_validate(args)
    rows = summary_mod.render_findings_only(result)
    if rows:
        print(rows)
    return result.exit_code


def cmd_gate(args: argparse.Namespace) -> int:
    """Machine-only gate check: prints a single token, returns the exit code."""
    try:
        result = _load_and_validate(args)
    except CdvError as exc:
        print("CDV_GATE=FAIL")
        print(f"CDV_ERROR_RULE={exc.rule}")
        print(f"CDV_ERROR={exc.message}")
        return EXIT_FAIL
    print(f"CDV_GATE={result.gate}")
    return result.exit_code


def cmd_context_proof(args: argparse.Namespace) -> int:
    """Establish that this process is observing the intended target.

    Machine-enforced verification context integrity. A behavioural check must not
    be interpreted until this reports PASS: absence of an effect is only evidence
    of blocking if the observation was of the right place. See
    ``core/cdv/context.py`` for the false PASS that this exists to prevent.
    """
    from . import context as context_mod

    project = os.path.abspath(args.project or os.getcwd())
    proof = context_mod.prove(
        project,
        expect_realpath=args.expect_realpath,
        expect_commit=args.expect_commit,
        marker_nonce=args.require_marker,
        observed_project=args.observed_project,
        require_guard_hooks=args.require_guard_hooks,
        require_pwd=args.require_pwd,
        allow_inherited_only=args.allow_inherited_only,
    )

    if args.json:
        print(json.dumps(proof.to_dict(), indent=2, sort_keys=True))
    else:
        print(f"CDV_CONTEXT_PROOF={proof.status}")
        print(f"CDV_CONTEXT_TARGET={proof.target}")
        print(f"CDV_CONTEXT_EXPECTED={proof.expected_realpath}")
        print(f"CDV_CONTEXT_AGREEING={len(proof.agreeing)}")
        print(f"CDV_CONTEXT_INDEPENDENT_AGREEING={len(proof.independent_agreeing)}")
        for reason in proof.reasons:
            print(f"CDV_CONTEXT_REASON={reason}")
        if args.verbose:
            print(proof.render())

    return EXIT_PASS if proof.status == context_mod.PASS else EXIT_FAIL


def cmd_rules(args: argparse.Namespace) -> int:
    """List every rule id the engine can emit.

    The canary suite asserts against this list, which means a rule that is
    renamed or removed without updating the tests is caught immediately.
    """
    from . import errors as E

    rules = sorted(
        {
            value
            for name, value in vars(E).items()
            if name.startswith("RULE_") and isinstance(value, str)
        }
    )
    if args.json:
        print(json.dumps(rules, indent=2))
    else:
        for rule in rules:
            print(rule)
    return EXIT_PASS


def cmd_version(args: argparse.Namespace) -> int:
    if args.json:
        print(json.dumps({"engine": __version__, "schema": SCHEMA_VERSION}))
    else:
        print(f"cdv {__version__} (schema v{SCHEMA_VERSION})")
    return EXIT_PASS


def _hash_tree(directory: str, limit_bytes: int = 64 * 1024 * 1024) -> tuple[str, int]:
    """Content hash over the project's files, for non-git projects.

    Bounded and deterministic. Excludes VCS and the installed verification
    runtime, so that installing or updating the guard does not by itself
    invalidate a project's recorded evidence state.
    """
    digest = hashlib.sha256()
    count = 0
    total = 0
    skip_dirs = {".git", ".verification", "node_modules", "__pycache__", ".venv", "venv"}
    for root, dirs, files in os.walk(directory):
        dirs[:] = sorted(d for d in dirs if d not in skip_dirs)
        for name in sorted(files):
            path = os.path.join(root, name)
            rel = os.path.relpath(path, directory)
            try:
                size = os.path.getsize(path)
            except OSError:
                continue
            if total + size > limit_bytes:
                return f"sha256:partial:{digest.hexdigest()[:32]}", count
            digest.update(rel.encode("utf-8", "replace"))
            digest.update(b"\x00")
            try:
                with open(path, "rb") as handle:
                    while True:
                        chunk = handle.read(1 << 20)
                        if not chunk:
                            break
                        digest.update(chunk)
            except OSError:
                continue
            digest.update(b"\x00")
            total += size
            count += 1
    return f"sha256:{digest.hexdigest()}", count


def _git(directory: str, *argv: str) -> str | None:
    try:
        proc = subprocess.run(
            ["git", "-C", directory, *argv],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0:
        return None
    return proc.stdout.strip()


def cmd_fingerprint(args: argparse.Namespace) -> int:
    """Report the state under evaluation, in the shape ``verification.yaml`` wants.

    The point of this command is that the recorded state is produced by reading
    the repository, not typed in by hand. Hand-typed state is exactly how stale
    evidence passes for current evidence.
    """
    directory = os.path.abspath(args.project or os.getcwd())

    commit = _git(directory, "rev-parse", "HEAD")
    tree = _git(directory, "rev-parse", "HEAD^{tree}")
    status = _git(directory, "status", "--porcelain")

    payload: dict[str, Any] = {
        "directory": directory,
        "vcs": "git" if commit else "none",
        "commit": commit,
        "tree_hash": tree,
        "dirty": bool(status),
    }
    if not commit:
        content_hash, file_count = _hash_tree(directory)
        payload["tree_hash"] = content_hash
        payload["file_count"] = file_count

    if args.json:
        print(json.dumps(payload, indent=2))
    else:
        print(f"CDV_FINGERPRINT_VCS={payload['vcs']}")
        print(f"CDV_FINGERPRINT_COMMIT={payload['commit'] or 'NONE'}")
        print(f"CDV_FINGERPRINT_TREE={payload['tree_hash'] or 'NONE'}")
        print(f"CDV_FINGERPRINT_DIRTY={'YES' if payload['dirty'] else 'NO'}")
        print("# paste into verification.yaml:")
        print("state:")
        print(f"  commit: {payload['commit'] or 'UNKNOWN'}")
        print(f"  tree_hash: {payload['tree_hash'] or 'UNKNOWN'}")
    return EXIT_PASS


def cmd_init(args: argparse.Namespace) -> int:
    """Write a starter verification.yaml, refusing to clobber an existing one."""
    target_dir = os.path.abspath(args.project or os.getcwd())
    target = os.path.join(target_dir, args.name or DEFAULT_FILENAME)

    if os.path.exists(target) and not args.force:
        print(f"cdv init: refusing to overwrite existing '{target}'", file=sys.stderr)
        return EXIT_USAGE

    template_path = args.template
    if not template_path:
        here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        template_path = os.path.join(here, "templates", "verification.yaml")
        if not os.path.isfile(template_path):
            # Installed layout: templates live beside the installed core.
            template_path = os.path.join(
                os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                "templates",
                "verification.yaml",
            )

    if not os.path.isfile(template_path):
        raise CdvError(
            "TEMPLATE_MISSING",
            f"template not found at '{template_path}'; pass --template explicitly",
            path=template_path,
        )

    with open(template_path, "r", encoding="utf-8") as handle:
        body = handle.read()

    # Stamp the real evaluated state if we can, so the first document the project
    # ever sees is already correctly bound rather than a placeholder.
    commit = _git(target_dir, "rev-parse", "HEAD")
    tree = _git(target_dir, "rev-parse", "HEAD^{tree}")
    if commit and tree:
        body = body.replace("__COMMIT__", commit).replace("__TREE_HASH__", tree)
    elif not commit:
        content_hash, _ = _hash_tree(target_dir)
        body = body.replace("__COMMIT__", "UNCOMMITTED").replace(
            "__TREE_HASH__", content_hash
        )

    os.makedirs(target_dir, exist_ok=True)
    with open(target, "w", encoding="utf-8") as handle:
        handle.write(body)

    print(f"cdv init: wrote {target}")
    return EXIT_PASS


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="cdv",
        description=(
            "Claim-Driven Verification engine. Evaluates a project's "
            "verification.yaml and decides whether each completion claim is "
            "sufficiently supported."
        ),
    )
    parser.add_argument("--version", action="version", version=f"cdv {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    def add_common(p: argparse.ArgumentParser) -> None:
        p.add_argument(
            "path",
            nargs="?",
            help=f"path to the verification document (default: search upward for {DEFAULT_FILENAME})",
        )
        p.add_argument(
            "--project",
            help="project directory used for oracle execution and document search",
        )
        p.add_argument(
            "--allow-execute-oracles",
            action="store_true",
            help=(
                "permit execution of project-supplied oracle qualification commands. "
                "Required for oracles declaring `mode: executable`; without it those "
                "oracles are reported unverified and block critical claims."
            ),
        )

    p_validate = sub.add_parser("validate", help="full report and verdict")
    add_common(p_validate)
    p_validate.add_argument("--json", action="store_true")
    p_validate.add_argument("--keyvalues", action="store_true")
    p_validate.add_argument("--findings", action="store_true", help="one line per finding")
    p_validate.add_argument("--verbose", "-v", action="store_true")
    p_validate.set_defaults(func=cmd_validate)

    p_summary = sub.add_parser("summary", help="key=value summary only")
    add_common(p_summary)
    p_summary.add_argument("--json", action="store_true")
    p_summary.set_defaults(func=cmd_summary)

    p_findings = sub.add_parser("findings", help="one line per finding")
    add_common(p_findings)
    p_findings.set_defaults(func=cmd_findings)

    p_gate = sub.add_parser("gate", help="print CDV_GATE=<verdict> only")
    add_common(p_gate)
    p_gate.set_defaults(func=cmd_gate)

    p_rules = sub.add_parser("rules", help="list emittable rule ids")
    p_rules.add_argument("--json", action="store_true")
    p_rules.set_defaults(func=cmd_rules)

    p_ctx = sub.add_parser(
        "context-proof",
        help=(
            "prove this process is observing the intended target before any "
            "behavioural result is interpreted"
        ),
    )
    p_ctx.add_argument("--project", help="the intended target project (absolute)")
    p_ctx.add_argument(
        "--expect-realpath",
        help="the identity the caller believes the target resolves to",
    )
    p_ctx.add_argument("--expect-commit", help="the commit under evaluation")
    p_ctx.add_argument(
        "--require-marker",
        help=(
            "a per-run nonce expected in <project>/.verification/context-marker. "
            "Written by the caller, so a match cannot have been produced by the "
            "agent under test."
        ),
    )
    p_ctx.add_argument(
        "--observed-project",
        help="the project the runtime itself reported using",
    )
    p_ctx.add_argument(
        "--require-guard-hooks",
        action="store_true",
        help="require that the guard's hooks wrote to the target's audit log",
    )
    p_ctx.add_argument(
        "--require-pwd",
        action="store_true",
        help="treat an unset PWD as a contradiction rather than as absent",
    )
    p_ctx.add_argument(
        "--allow-inherited-only",
        action="store_true",
        help=(
            "accept a proof built only from cwd and PWD. Weaker: both can be "
            "inherited stale, which is the exact defect this check exists to catch."
        ),
    )
    p_ctx.add_argument("--json", action="store_true")
    p_ctx.add_argument("--verbose", "-v", action="store_true")
    p_ctx.set_defaults(func=cmd_context_proof)

    p_version = sub.add_parser("version", help="engine and schema version")
    p_version.add_argument("--json", action="store_true")
    p_version.set_defaults(func=cmd_version)

    p_fp = sub.add_parser("fingerprint", help="report the state under evaluation")
    p_fp.add_argument("--project", default=None)
    p_fp.add_argument("--json", action="store_true")
    p_fp.set_defaults(func=cmd_fingerprint)

    p_init = sub.add_parser("init", help="write a starter verification.yaml")
    p_init.add_argument("--project", default=None)
    p_init.add_argument("--name", default=None)
    p_init.add_argument("--template", default=None)
    p_init.add_argument("--force", action="store_true")
    p_init.set_defaults(func=cmd_init)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except CdvError as exc:
        # Fail closed, loudly, on stderr, with a stable machine-readable rule id.
        print(f"cdv: error [{exc.rule}]: {exc.message}", file=sys.stderr)
        if exc.detail:
            print(f"cdv: detail: {json.dumps(exc.detail, sort_keys=True)}", file=sys.stderr)
        if exc.rule == "DEPENDENCY_MISSING_PYYAML":
            return EXIT_DEPENDENCY
        return EXIT_FAIL
    except KeyboardInterrupt:  # pragma: no cover
        print("cdv: interrupted", file=sys.stderr)
        return EXIT_FAIL
    except Exception as exc:  # noqa: BLE001 - the gate must never crash an adapter
        # An unexpected internal failure is a fail-closed condition. Reporting it
        # as GATE=FAIL with a distinct rule means a broken engine can never be
        # mistaken for a passing verification.
        import traceback

        print(
            f"cdv: internal error [{type(exc).__name__}]: {exc}",
            file=sys.stderr,
        )
        traceback.print_exc(file=sys.stderr)
        print("CDV_GATE=FAIL")
        print("CDV_ERROR_RULE=ENGINE_INTERNAL_ERROR")
        return EXIT_FAIL


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
