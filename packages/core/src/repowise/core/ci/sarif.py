"""SARIF 2.1.0 envelopes for code-scanning upload. Features supply rules and results."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any
from urllib.parse import quote

SCHEMA = "https://json.schemastore.org/sarif-2.1.0.json"
INFORMATION_URI = "https://repowise.dev"


def rule(rule_id: str, name: str, short: str, full: str, help_text: str | None = None) -> dict:
    """One ``reportingDescriptor``."""
    out: dict[str, Any] = {
        "id": rule_id,
        "name": name,
        "shortDescription": {"text": short},
        "fullDescription": {"text": full},
    }
    if help_text:
        out["help"] = {"text": help_text}
    return out


def result(
    rule_id: str,
    level: str,
    message: str,
    path: str,
    line: int | None,
    fingerprint_key: str,
    fingerprint: str,
    properties: Mapping[str, Any] | None = None,
    *,
    suppressed: bool = False,
) -> dict:
    """One result, located relative to ``%SRCROOT%`` with a percent-encoded URI.

    *line* ``None`` locates the result on the file alone, for a finding whose
    line belongs to another revision. *suppressed* marks one accepted outside
    the log (a baseline), so code scanning shows it closed rather than open.
    """
    location: dict[str, Any] = {
        "artifactLocation": {
            "uri": quote(path.replace("\\", "/").lstrip("/"), safe="/"),
            "uriBaseId": "%SRCROOT%",
        }
    }
    if line is not None:
        location["region"] = {"startLine": max(1, int(line))}
    out: dict[str, Any] = {
        "ruleId": rule_id,
        "level": level,
        "message": {"text": message},
        "locations": [{"physicalLocation": location}],
        "partialFingerprints": {fingerprint_key: fingerprint},
    }
    if suppressed:
        out["suppressions"] = [{"kind": "external"}]
    if properties:
        out["properties"] = dict(properties)
    return out


def run(
    tool_name: str,
    version: str,
    rules: Sequence[Mapping[str, Any]],
    results: Sequence[Mapping[str, Any]],
) -> dict:
    """A complete log with one run; ``ruleIndex`` is filled for known rule ids."""
    index = {r["id"]: i for i, r in enumerate(rules)}
    indexed = []
    for res in results:
        res = dict(res)
        if res["ruleId"] in index:
            res["ruleIndex"] = index[res["ruleId"]]
        indexed.append(res)
    return {
        "$schema": SCHEMA,
        "version": "2.1.0",
        "runs": [
            {
                "tool": {
                    "driver": {
                        "name": tool_name,
                        "version": version,
                        "informationUri": INFORMATION_URI,
                        "rules": [dict(r) for r in rules],
                    }
                },
                "results": indexed,
            }
        ],
    }
