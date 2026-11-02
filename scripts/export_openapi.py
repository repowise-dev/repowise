"""Export the server's OpenAPI document for a release, with a stability label per route.

The release workflow attaches the output to each GitHub release as
``openapi.json``, so anyone building on ``repowise serve`` can fetch one file
that matches the version they run.

Every operation gets ``x-repowise-stability``:

- ``stable``: an integration surface. Breaking changes are named in the
  changelog.
- ``internal``: a payload shaped for the bundled dashboard. It can change in
  any release without notice.

The label comes from the operation's first tag. A tag missing from both sets
fails the export, so a new router has to be classified before it ships.

Run ``python scripts/export_openapi.py --out openapi.json``.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys
from typing import Any

STABLE_TAGS = frozenset(
    {
        "blast-radius",
        "c4",
        "changes",
        "chat",
        "code-health",
        "coupling",
        "dead-code",
        "decisions",
        "doc-drift",
        "events",
        "external-systems",
        "git",
        "graph",
        "health",
        "jobs",
        "pages",
        "refactoring",
        "repos",
        "search",
        "security",
        "symbols",
        "webhooks",
        "workspace",
    }
)

INTERNAL_TAGS = frozenset(
    {
        "actions",
        "areas",
        "claude-md",
        "costs",
        "episodes",
        "feedback",
        "files",
        "history",
        "knowledge-map",
        "mcp",
        "meta",
        "modules",
        "overview",
        "owners",
        "platform",
        "providers",
        "setup",
        "stats",
    }
)

_HTTP_METHODS = frozenset({"get", "put", "post", "delete", "patch", "options", "head", "trace"})


def label(spec: dict[str, Any]) -> dict[str, Any]:
    """Stamp each operation with its stability, in place. Raise on an unclassified tag."""
    unclassified: set[str] = set()
    for item in spec.get("paths", {}).values():
        for method, op in item.items():
            if method not in _HTTP_METHODS:
                continue
            tags = op.get("tags") or []
            tag = tags[0] if tags else "(untagged)"
            if tag in STABLE_TAGS:
                op["x-repowise-stability"] = "stable"
            elif tag in INTERNAL_TAGS:
                op["x-repowise-stability"] = "internal"
            else:
                unclassified.add(tag)
    if unclassified:
        raise ValueError(
            "Classify these OpenAPI tags in scripts/export_openapi.py: " + ", ".join(sorted(unclassified))
        )
    return spec


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=pathlib.Path, default=pathlib.Path("openapi.json"))
    args = parser.parse_args(argv)

    from repowise.server.app import create_app

    spec = label(create_app().openapi())
    args.out.write_text(json.dumps(spec, indent=2, sort_keys=False) + "\n", encoding="utf-8")
    print(f"wrote {args.out} ({len(spec.get('paths', {}))} paths)", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
