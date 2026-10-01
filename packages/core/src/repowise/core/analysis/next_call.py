"""What to call next: one shape for every surface that hands an agent its next call.

Actions, Fix first, the ``get_health`` pillar summaries and the ``get_risk`` PR
directive all carry :class:`ActionCommand`. It lives in a leaf module because
``analysis.health.fix_first`` needs it and ``analysis.actions`` imports
``fix_first`` back.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any


def _value(v: Any) -> str:
    # bool before int: ``True`` is an int too.
    if v is None or isinstance(v, bool):
        return repr(v)
    if isinstance(v, (int, float)):
        return str(v)
    if isinstance(v, Mapping):
        return "{" + ", ".join(f"{_value(str(k))}: {_value(x)}" for k, x in v.items()) + "}"
    if isinstance(v, (list, tuple)):
        return "[" + ", ".join(_value(x) for x in v) + "]"
    return json.dumps(str(v), ensure_ascii=False)


def render_call(tool: str, arguments: Mapping[str, Any] | None = None) -> str:
    """``tool(key=value, ...)`` as an agent types it: strings double-quoted, lists
    ``["a", "b"]``, ``True``/``None`` as Python literals, numbers plain."""
    args = ", ".join(f"{k}={_value(v)}" for k, v in (arguments or {}).items())
    return f"{tool}({args})"


@dataclass(frozen=True, slots=True)
class ActionCommand:
    """A way to see more: the MCP call an agent makes, the CLI line a person runs.

    ``tool`` and ``arguments`` are the structured call; ``mcp`` is that call
    rendered by :func:`render_call`. Build one with :meth:`call`.
    """

    purpose: str
    mcp: str | None = None
    cli: str | None = None
    tool: str | None = None
    arguments: Mapping[str, Any] | None = None

    @classmethod
    def call(
        cls,
        purpose: str,
        tool: str,
        arguments: Mapping[str, Any] | None = None,
        *,
        cli: str | None = None,
    ) -> ActionCommand:
        arguments = dict(arguments or {})
        return cls(purpose, render_call(tool, arguments), cli, tool, arguments)

    def as_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"purpose": self.purpose, "mcp": self.mcp, "cli": self.cli}
        if self.tool is not None:
            out["tool"] = self.tool
            out["arguments"] = dict(self.arguments or {})
        return out
