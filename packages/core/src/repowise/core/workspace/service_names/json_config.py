"""A JSON config file as .NET reads it: ``//`` and ``/* */`` comments and trailing commas allowed."""

from __future__ import annotations

import json
import re
from typing import Any

_TRAILING_COMMA_RE = re.compile(r",\s*([}\]])")


def load_json_config(text: str) -> Any:
    """The file's value, or None when it does not parse even with comments removed."""
    try:
        return json.loads(text)
    except ValueError:
        pass
    from repowise.core.ingestion.tsconfig_resolver import _strip_jsonc_comments

    try:
        return json.loads(_TRAILING_COMMA_RE.sub(r"\1", _strip_jsonc_comments(text)))
    except ValueError:
        return None


def field(node: Any, name: str) -> Any:
    """*node*'s value under *name* (lowercase), case-insensitive as .NET configuration binds it."""
    if not isinstance(node, dict):
        return None
    return next((v for k, v in node.items() if str(k).lower() == name), None)
