"""GitLab Code Quality reports: the JSON issue list a merge request's widget reads.

Features supply check names, severities and messages; this module fixes the
shape, names the tool and applies the baseline.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

#: The severities the format defines, least to most severe. Checked because
#: GitLab drops a report holding an unknown severity without saying so.
SEVERITIES = ("info", "minor", "major", "critical", "blocker")


def issue(
    check_name: str,
    severity: str,
    description: str,
    path: str,
    line: int | None,
    fingerprint: str,
) -> dict:
    """One issue on a repo-relative *path*; *line* ``None`` places it on line 1."""
    if severity not in SEVERITIES:
        raise ValueError(f"unknown Code Quality severity {severity!r}")
    return {
        "description": description,
        "check_name": check_name,
        "fingerprint": fingerprint,
        "severity": severity,
        "location": {
            "path": path.replace("\\", "/").lstrip("/"),
            "lines": {"begin": max(1, int(line or 1))},
        },
    }


def report(
    tool: str,
    issues: Iterable[Mapping[str, Any]],
    *,
    accepted: frozenset[str] = frozenset(),
) -> list[dict]:
    """*issues* in order as ``<tool>/<check>``, each fingerprint made unique.

    An issue whose fingerprint is in *accepted* (a baseline) is left out: the
    format has no suppression field, so the widget would list it as open.

    GitLab matches issues across pipelines by fingerprint, so two sharing one
    would read as a single issue. A repeat becomes ``<fingerprint>:<n>``, *n*
    counting from 2 in the order given, which is stable when the caller sorts.
    """
    seen: set[str] = set()
    out = []
    for item in issues:
        if item["fingerprint"] in accepted:
            continue
        item = dict(item)
        item["check_name"] = f"{tool}/{item['check_name']}"
        base = fp = item["fingerprint"]
        n = 1
        while fp in seen:
            n += 1
            fp = f"{base}:{n}"
        seen.add(fp)
        item["fingerprint"] = fp
        out.append(item)
    return out
