"""Azure Functions (C#) HTTP trigger dialect.

A function serves HTTP through the trigger attribute on its request parameter,
in both the in-process and the isolated worker model::

    [FunctionName("GetOrder")]   // isolated: [Function("GetOrder")]
    public static IActionResult Run(
        [HttpTrigger(AuthorizationLevel.Function, "get", Route = "orders/{id}")] HttpRequest req)

The route is ``Route``, or the function's name when the attribute sets none,
served under the host's route prefix. A trigger that lists no method accepts
every verb, so like Express ``all`` it names no single route and is skipped.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from repowise.core.ingestion.framework_routes import HTTP_METHODS

from ..base import line_at
from ..calls import Call, call_sites
from ..langs import CSHARP
from ..strings import Arg, select_argument
from .dialect import build_provider_contract
from .mounts import compose_prefix

if TYPE_CHECKING:
    from repowise.core.workspace.contracts import Contract

    from ..base import ScanContext
    from ..calls import FileStrings

_TRIGGER = Call(
    head=re.compile(r"\[\s*(?:[\w.]+\.)?HttpTrigger(?:Attribute)?\s*\("),
    extensions=CSHARP,
    requires=("HttpTrigger",),
)
# [FunctionName("X")] (in-process) and [Function("X")] (isolated worker).
_NAME = Call(
    head=re.compile(r"\[\s*(?:[\w.]+\.)?(?:FunctionName|Function)(?:Attribute)?\s*\("),
    extensions=CSHARP,
    requires=("Function",),
)
_ROUTE = Arg(keys=("Route",))
_NAMEOF_RE = re.compile(r"nameof\s*\(\s*(?:[\w.]+\.)?(\w+)\s*\)")
_PROPERTY_RE = re.compile(r"\w+\s*=(?!=)")

# The host's default `extensions.http.routePrefix`. Ceiling: a host.json that
# changes it is not read (a .json walk for one key), so such an app's routes
# miss their callers rather than link wrongly; read host.json when an estate
# shows it.
_ROUTE_PREFIX = "api"


def _function_name(args: list[str], strings: FileStrings) -> str | None:
    if not args:
        return None
    m = _NAMEOF_RE.fullmatch(args[0])
    return m.group(1) if m else strings.text(args[0])


def _own_name(content: str, names: list[tuple[int, str | None]], trigger: int) -> str | None:
    """The name attribute of the function whose parameter holds *trigger*.

    The last one before it, and only when no member body or statement
    (``{`` / ``;``) lies between, so a timer function's or any other
    member's name is never borrowed.
    """
    before = [n for n in names if n[0] < trigger]
    if not before:
        return None
    start, name = before[-1]
    gap = content[start:trigger]
    return None if "{" in gap or ";" in gap else name


class AzureFunctionsDialect:
    name = "azure-functions"
    extensions = CSHARP
    requires = _TRIGGER.requires

    def extract(self, ctx: ScanContext) -> list[Contract]:
        triggers = list(call_sites(ctx, (_TRIGGER,), empty=True))
        if not triggers:
            return []
        strings = triggers[0][3]
        # Unreadable names stay in (as None) so they still claim their function.
        names = sorted(
            (m.start(), _function_name(args, strings))
            for _, args, m, _ in call_sites(ctx, (_NAME,), strings)
        )

        out: list[Contract] = []
        for _, args, m, _ in triggers:
            verbs = {
                text.upper()
                for a in args
                if not _PROPERTY_RE.match(a) and (text := strings.text(a))
            } & HTTP_METHODS
            if not verbs:
                continue
            route_expr = select_argument(args, _ROUTE)
            route = strings.text(route_expr[0]) if route_expr and route_expr[0] != "null" else ""
            if route == "":  # unset or empty: Azure serves the function's name
                route = _own_name(ctx.content, names, m.start())
            if route is None or "${" in route:
                continue  # a route this file cannot spell
            for verb in sorted(verbs):
                c = build_provider_contract(
                    ctx,
                    method=verb,
                    path_raw=compose_prefix(_ROUTE_PREFIX, route),
                    framework="azure-functions",
                    line=line_at(ctx.content, m.start()),
                )
                if c is not None:
                    out.append(c)
        return out
