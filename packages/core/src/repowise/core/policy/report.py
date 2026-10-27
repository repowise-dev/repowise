"""Policy results as SARIF 2.1.0 and JUnit XML.

SARIF carries one result per rule that fired, so code scanning on any forge
and reviewdog show it on the change: ``fail`` is an ``error``, every other
action a ``warning``, and the action, components, owners and the basis of each
evidence kind ride in the result's ``properties``. A result with no file of
its own is located on the policy file. Rules that could not be evaluated are
listed in the run's properties, never as results.

JUnit has one test case per rule: a ``failure`` for a ``fail`` that fired,
``skipped`` for a rule whose evidence was not available, and a pass with the
message in ``system-out`` for every other action that fired.
"""

from __future__ import annotations

import hashlib
import xml.etree.ElementTree as ET
from collections.abc import Sequence
from pathlib import PurePath, PureWindowsPath
from typing import Any

from repowise.core.ci import sarif
from repowise.core.policy.rules import POLICIES_FILE, TRIGGERED, UNKNOWN, Policy, RuleResult

TOOL_NAME = "repowise-check"
FINGERPRINT_KEY = "repowisePolicy/v1"


def _rule_id(name: str) -> str:
    return f"policy/{name}"


def render_sarif(
    policy: Policy,
    results: Sequence[RuleResult],
    *,
    tool_version: str,
    revspec: str,
    fallback_path: str | None = None,
) -> dict:
    """*fallback_path* (repo-relative) locates file-less results when the policy
    file lies outside the repository, so every uri stays relative to it."""
    anchor = policy.source
    if PurePath(anchor).is_absolute() or PureWindowsPath(anchor).is_absolute():
        anchor = fallback_path or POLICIES_FILE
    rules = [
        sarif.rule(
            _rule_id(r.name),
            r.name,
            r.description or f"Change policy rule {r.name}",
            r.description or f"Change policy rule {r.name} ({r.gate or 'conditions'}, {r.action}).",
        )
        for r in policy.rules
    ]
    out: list[dict] = []
    for res in results:
        if res.outcome != TRIGGERED:
            continue
        properties: dict[str, Any] = {"action": res.rule.action}
        for key in ("components", "owners", "reasons"):
            if getattr(res, key):
                properties[key] = getattr(res, key)
        if res.evidence:
            properties["evidence"] = res.evidence
        for path, line, own in res.locations or [(anchor, None, None)]:
            # Line-free like every gate's own fingerprint, so an edit above keeps it.
            ident = own or path
            out.append(
                sarif.result(
                    _rule_id(res.rule.name),
                    "error" if res.rule.action == "fail" else "warning",
                    res.message,
                    path,
                    line,
                    FINGERPRINT_KEY,
                    hashlib.sha256(f"{res.rule.name}|{ident}".encode()).hexdigest()[:32],
                    properties,
                )
            )
    not_evaluated = [r.rule.name for r in results if r.outcome == UNKNOWN]
    run_props: dict[str, Any] = {"revspec": revspec, "policyFile": policy.source}
    if not_evaluated:
        run_props["notEvaluated"] = not_evaluated
    return sarif.run(TOOL_NAME, tool_version, rules, out, properties=run_props)


def render_junit(results: Sequence[RuleResult], *, revspec: str) -> str:
    suite = ET.Element(
        "testsuite",
        name=TOOL_NAME,
        tests=str(len(results)),
        failures=str(sum(r.fails for r in results)),
        errors="0",
        skipped=str(sum(r.outcome == UNKNOWN for r in results)),
    )
    ET.SubElement(ET.SubElement(suite, "properties"), "property", name="revspec", value=revspec)
    for res in results:
        case = ET.SubElement(suite, "testcase", name=res.rule.name, classname=f"{TOOL_NAME}.{res.rule.action}")
        if res.fails:
            failure = ET.SubElement(case, "failure", message=res.message, type=res.rule.action)
            failure.text = "\n".join(f"{p}{f':{ln}' if ln else ''}" for p, ln, _fp in res.locations) or None
        elif res.outcome == UNKNOWN:
            ET.SubElement(case, "skipped", message=res.message)
        elif res.outcome == TRIGGERED:
            ET.SubElement(case, "system-out").text = f"{res.rule.action}: {res.message}"
    root = ET.Element("testsuites", name=TOOL_NAME)
    root.append(suite)
    ET.indent(root)
    return '<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(root, encoding="unicode") + "\n"
