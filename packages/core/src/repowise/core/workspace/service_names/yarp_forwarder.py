"""YARP direct forwarding: ``app.MapForwarder(pattern, destination[, targetTemplate])``.

``MapForwarder("/api/{**rest}", "http://catalog-api")`` forwards calls under
``/api`` unchanged to the service the destination names (Aspire's
``https+http://`` scheme included), after any base path it carries; a target
template ending in the pattern's own variables rewrites the prefix
(:func:`.base.template_upstream`). The gateway is the repo holding the file.

A forwarder the reader cannot rewrite through is still stated, with no
forwarded path: a pattern not ending in a catch-all (``{**rest}``), a template that moves or adds
segments (``/product-images/{id}`` to ``/api/catalog/items/{id}/pic``), or any
other third argument (a request config or transformer). Only string literals
are read, and only in ``Program.cs`` and ``Startup.cs``, where endpoints are
mapped; widening that gate is one glob.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

from .base import (
    PREFIX,
    Claim,
    Route,
    ServiceNameSource,
    SourceFile,
    host_of,
    literal_prefix,
    template_upstream,
)
from .yarp import base_path, catch_all

_FORWARDER_RE = re.compile(
    r"\.MapForwarder\s*\(\s*@?\"([^\"]*)\"\s*,\s*@?\"([^\"]*)\"\s*(?:,\s*@?\"([^\"]*)\"\s*)?([,)])"
)


def read(file: SourceFile) -> Iterable[Claim]:
    for m in _FORWARDER_RE.finditer(file.text):
        line = file.text[file.text.rfind("\n", 0, m.start()) + 1 : m.start()]
        if line.lstrip().startswith(("//", "/*", "*")):
            continue  # commented out
        pattern, destination, template, end = m.groups()
        prefix, kind = literal_prefix(pattern)
        if not prefix:
            continue  # a catch-all is never more specific than a real route
        upstream: str | None = None
        base = base_path(destination)
        if end == ")" and kind == PREFIX and catch_all(pattern) and base is not None:
            upstream = prefix if template is None else template_upstream(pattern, template)
        yield Claim(
            name=host_of("destination", destination) or "",
            route=Route(prefix, None if upstream is None or base is None else base + upstream),
        )


SOURCE = ServiceNameSource(
    name="yarp_forwarder",
    filenames=("Program.cs", "Startup.cs"),
    marker="MapForwarder",
    read=read,
)
