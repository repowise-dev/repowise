"""``repowise check``: evaluate ``.repowise/policies.yaml`` against a change, in CI.

Each rule either tests conditions over the change's evidence (components and
their tags, provenance, reaching tests, contract breaks, decisions crossed,
bus factor) or wraps an existing gate (``security``, ``coverage``,
``doc_drift``), which runs as its own command with the options the rule gives.
Those commands are untouched: without a policy file they behave as they always
have, and ``repowise check`` reports that there is nothing to evaluate.

Exit codes follow :mod:`repowise.cli.ci`: 0 nothing failed, 1 a ``fail`` rule
fired, 2 the policy is invalid, the change cannot be read, or a ``fail`` gate
could not be evaluated.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import click

from repowise.cli.ci import (
    CannotEvaluateError,
    cannot_evaluate,
    change_set,
    ci_notices,
    ci_revspec,
    repo_root,
)
from repowise.cli.output import emit_json, format_option

_FORMATS = ("table", "json", "sarif", "junit")

#: Gate kind -> (argv after ``repowise``, "module:command" for option checks, takes REVSPEC).
_GATES: dict[str, tuple[tuple[str, ...], str, bool]] = {
    "security": (("security", "check"), "security_cmd:security_command", True),
    "coverage": (("coverage", "check"), "coverage_check_cmd:coverage_check", True),
    "doc_drift": (("doc-drift", "--check"), "doc_drift_cmd:doc_drift_command", False),
}
#: A gate still running after this is reported as not evaluated.
GATE_TIMEOUT_SECONDS = 1800
#: Options ``repowise check`` sets itself, or that would write files.
_RESERVED = frozenset(
    {"format", "path", "repo", "staged", "write_baseline", "check", "json", "no_workspace"}
)


@click.command("check")
@click.argument("revspec", required=False, default=None)
@click.option("--path", "repo", default=None, help="A path inside the repository (defaults to cwd).")
@click.option(
    "--policies",
    "policies_path",
    default=None,
    help="Policy file to evaluate (default .repowise/policies.yaml).",
)
@click.option(
    "--sarif-file",
    type=click.Path(dir_okay=False, path_type=Path),
    default=None,
    help="Also write SARIF 2.1.0 here, for code scanning or reviewdog.",
)
@click.option(
    "--junit-file",
    type=click.Path(dir_okay=False, path_type=Path),
    default=None,
    help="Also write JUnit XML here, for a CI test report.",
)
@format_option(
    choices=_FORMATS,
    help="Output format. ``json`` carries the evidence too (usable as OPA input).",
)
def check_command(
    revspec: str | None,
    repo: str | None,
    policies_path: str | None,
    sarif_file: Path | None,
    junit_file: Path | None,
    fmt: str,
) -> None:
    """Evaluate the change policy in .repowise/policies.yaml against a change.

    REVSPEC is the change (``origin/main...HEAD``, ``base..head`` or one
    commit); without it, the CI's pull-request target branch...HEAD, else the
    remote's default branch...HEAD.
    """
    from repowise.core.policy import PolicyError, load_policy

    try:
        root = repo_root(repo)
        policy = load_policy(root, policies_path)
        if policy is None:
            _emit_nothing(fmt, sarif_file, junit_file)
            return
        _check_gate_options(policy)
        resolved = ci_revspec(str(root), revspec)
        change = change_set(str(root), resolved)
    except PolicyError as exc:
        cannot_evaluate(fmt, "policy_invalid", "\n".join([f"{exc.source} is invalid:", *exc.problems]))
    except CannotEvaluateError as exc:
        cannot_evaluate(fmt, exc.code, str(exc))

    from repowise.core.policy import evaluate, exit_code

    gate_runs = {r.name: _run_gate(root, r, resolved) for r in policy.rules if r.gate}
    evidence = _gather(root, change) if any(not r.gate for r in policy.rules) else None
    results = evaluate(policy, evidence, gate_runs)
    anchor = min([*change.files, *change.deleted], default=None)
    _emit(fmt, policy, results, evidence, change.label, sarif_file, junit_file, anchor=anchor)
    code = exit_code(results)
    if code:
        raise click.exceptions.Exit(code)


def _gather(root: Path, change: Any) -> dict[str, Any]:
    # Interim: swap for the Change Record evidence builder
    # (repowise.core.changes.evidence) once it lands; the shape is the same.
    from repowise.cli.helpers import repo_index_session, run_async
    from repowise.core.policy.interim_evidence import gather_evidence

    async def go() -> dict[str, Any]:
        async with repo_index_session(root) as opened:
            if opened is None:
                return await gather_evidence(root, change)
            session, repo_id = opened
            return await gather_evidence(root, change, session=session, repo_id=repo_id)

    return run_async(go())


# -- gates --------------------------------------------------------------------


def _gate_command(kind: str) -> click.Command:
    import importlib

    module, _, attr = _GATES[kind][1].partition(":")
    command = getattr(importlib.import_module(f"repowise.cli.commands.{module}"), attr)
    if isinstance(command, click.Group):
        command = command.commands[_GATES[kind][0][1]]
    return command


def _check_gate_options(policy: Any) -> None:
    """Every ``with`` key names an option of its gate's command, or the policy is invalid."""
    from repowise.core.policy import PolicyError

    problems = []
    for rule in policy.rules:
        if not rule.gate:
            continue
        flags = {
            opt.lstrip("-").replace("-", "_")
            for p in _gate_command(rule.gate).params
            if isinstance(p, click.Option)
            for opt in p.opts
            if opt.startswith("--")
        } - _RESERVED
        for key in rule.gate_args:
            if key not in flags:
                known = ", ".join(sorted(flags))
                problems.append(f"rule {rule.name}: the {rule.gate} gate has no option {key!r} (options: {known})")
    if problems:
        raise PolicyError(policy.source, problems)


def _gate_argv(rule: Any, revspec: str) -> list[str]:
    argv, _cmd, takes_revspec = _GATES[rule.gate]
    out = [*argv]
    args = dict(rule.gate_args)
    if not takes_revspec:
        # doc-drift judges the tree; scope it to this change unless the rule says otherwise.
        args.setdefault("since", revspec)
    for key, value in args.items():
        flag = "--" + key.replace("_", "-")
        if value is True:
            out.append(flag)
        elif value is False:
            continue
        else:
            # ``--flag=value`` so a value starting with "-" is never read as an option.
            out += [f"{flag}={v}" for v in (value if isinstance(value, list) else [value])]
    out += ["--format", "json"]
    # ``--`` ends the options, so REVSPEC is always the positional argument.
    return [*out, "--", revspec] if takes_revspec else out


def _verdict(kind: str, doc: Any) -> bool | None:
    """Whether the gate's own JSON verdict says it failed; ``None`` when there is none."""
    gate = doc.get("gate") if isinstance(doc, dict) else None
    if kind == "coverage":
        return gate == "fail" if isinstance(gate, str) else None
    passed = gate.get("passed") if isinstance(gate, dict) else None
    return (not passed) if isinstance(passed, bool) else None


def _run_gate(root: Path, rule: Any, revspec: str) -> Any:
    """Run the gate as its own command does, in a child process, and read its verdict.

    Only a JSON verdict that agrees with the exit code counts: a crash, a
    refusal or anything else unreadable is "could not be evaluated", never a
    fired rule.
    """
    import os

    from repowise.core.policy import GateRun

    argv = _gate_argv(rule, revspec)
    shown = "repowise " + " ".join(a for a in argv if a not in ("--format", "json", "--"))
    env = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"}
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "repowise.cli.main", *argv],
            cwd=root,
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            stdin=subprocess.DEVNULL,
            timeout=GATE_TIMEOUT_SECONDS,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return GateRun(2, f"{shown} did not finish within {GATE_TIMEOUT_SECONDS} seconds")
    except OSError as exc:
        return GateRun(2, f"could not start {shown}: {exc}")
    try:
        doc = json.loads(proc.stdout or "null")
    except ValueError:
        doc = None
    failed = _verdict(rule.gate, doc)
    if failed is None or proc.returncode != int(failed):
        message = doc.get("message") if isinstance(doc, dict) else None
        last = (proc.stderr.strip().splitlines() or [""])[-1]
        return GateRun(2, message or last or f"{shown} exited {proc.returncode} with no verdict")
    locations = []
    for f in (doc["gate"].get("failing") or ()) if isinstance(doc["gate"], dict) else ():
        line = None if f.get("commit") else f.get("line_number")
        locations.append((str(f.get("file_path")), line, f.get("fingerprint")))
    return GateRun(proc.returncode, f"Reproduce with: {shown}", tuple(locations))


# -- output -------------------------------------------------------------------


def _emit_nothing(fmt: str, sarif_file: Path | None, junit_file: Path | None) -> None:
    from repowise.core.policy import Policy

    notice = (
        "No .repowise/policies.yaml: nothing to evaluate. The security, coverage "
        "and doc-drift gates run on their own as before."
    )
    _emit(fmt, Policy(()), [], None, "", sarif_file, junit_file, notice=notice)


def _emit(
    fmt: str,
    policy: Any,
    results: list,
    evidence: dict | None,
    revspec: str,
    sarif_file: Path | None,
    junit_file: Path | None,
    *,
    notice: str | None = None,
    anchor: str | None = None,
) -> None:
    from repowise.cli import __version__
    from repowise.core.policy.report import render_junit, render_sarif

    sarif = render_sarif(
        policy, results, tool_version=__version__, revspec=revspec, fallback_path=anchor
    )
    junit = render_junit(results, revspec=revspec)
    if sarif_file:
        sarif_file.write_text(json.dumps(sarif, indent=2), encoding="utf-8")
    if junit_file:
        junit_file.write_text(junit, encoding="utf-8")
    if fmt == "json":
        emit_json(
            {
                "revspec": revspec,
                "policy_file": policy.source if policy.rules else None,
                "results": [r.to_dict() for r in results],
                "evidence": evidence,
            }
        )
    elif fmt == "sarif":
        emit_json(sarif)
    elif fmt == "junit":
        click.echo(junit, nl=False)
    if notice:
        ci_notices(fmt).print(notice)
    elif fmt == "table":
        _print_table(results, revspec)


_STYLE = {"triggered": "bold", "passed": "green", "not_applicable": "dim", "unknown": "yellow"}


def _print_table(results: list, revspec: str) -> None:
    from rich.markup import escape

    from repowise.cli.helpers import console

    console.print(f"Change policy for [bold]{escape(revspec)}[/bold]: {len(results)} rule(s)")
    for r in results:
        label = r.rule.action.upper() if r.outcome == "triggered" else r.outcome.replace("_", " ")
        style = "red" if r.fails else _STYLE[r.outcome]
        console.print(f"  [{style}]{escape(label):<22}[/{style}] {escape(r.rule.name)}: {escape(r.message)}")
