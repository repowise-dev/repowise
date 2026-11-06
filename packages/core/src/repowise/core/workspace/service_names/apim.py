"""Azure API Management: which path an APIM front door forwards to which service.

An APIM API serves calls under its ``path`` (``orders``) and forwards them to
its ``serviceUrl`` (``http://orders-service:8080/api``) with that path replaced
by the URL's own path, so ``/orders/1`` reaches ``/api/1`` on ``orders-service``.
The API is read from three declarations:

* a Bicep resource of type ``Microsoft.ApiManagement/service/apis`` (or a child
  ``apis`` resource nested in the service), its ``properties.path`` and
  ``properties.serviceUrl``;
* an ARM template resource of the same type, at any nesting depth;
* an APIOps ``apiInformation.json`` (``properties.path``, ``properties.serviceUrl``).

The gateway is the repo holding the file. The service is the ``serviceUrl``
host, resolved through the host map like any other route target, so a public
App Service host stays unmatched unless a workspace file maps it to a repo.

An API whose ``serviceUrl`` is not a literal URL (a Bicep parameter or
``${...}`` interpolation, an ARM ``[...]`` expression, or none at all) is still
stated, with no target and no forwarded path, so it keeps a shorter prefix from
claiming its calls. An API with an empty ``path`` is a catch-all and is
skipped. Ceiling: a ``set-backend-service base-url`` in policy XML and the
operations' ``urlTemplate`` are not read.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Iterator
from typing import Any
from urllib.parse import urlsplit

from .base import Claim, Route, ServiceNameSource, SourceFile, host_of, literal_prefix
from .json_config import field, load_json_config

_API_TYPE = "microsoft.apimanagement/service/apis"

#: The opening of a Bicep APIM API resource: the full type, or the ``apis``
#: child type inside the service resource; ``existing`` resources are skipped.
_BICEP_API_RE = re.compile(
    r"\bresource\s+\w+\s+'(?:Microsoft\.ApiManagement/service/)?apis(?:@[^']*)?'\s*=\s*\{",
    re.IGNORECASE,
)
_BICEP_PROPERTY_RE = {
    name: re.compile(rf"(?m)^\s*{name}\s*:\s*(?:'([^'\n]*)'|(\S[^\n]*))", re.IGNORECASE)
    for name in ("path", "serviceUrl")
}


def _route(path: str, service_url: str | None) -> Claim | None:
    """The claim for an API under *path* forwarding to *service_url*, None for a catch-all."""
    prefix, _ = literal_prefix(path)
    if not prefix:
        return None
    if service_url is None or "${" in service_url or service_url.startswith("["):
        return Claim(name="", route=Route(prefix, None))
    host = host_of("serviceurl", service_url) or ""
    try:
        base = urlsplit(service_url.strip()).path.strip().strip("/").lower()
    except ValueError:
        return Claim(name="", route=Route(prefix, None))
    return Claim(name=host, route=Route(prefix, "/" + base if base else "" if host else None))


def _block(text: str, start: int) -> str:
    """The text of the ``{...}`` block opening at *start*, quoted strings skipped."""
    depth = 0
    i = start
    while i < len(text):
        ch = text[i]
        if ch == "'":
            end = text.find("'", i + 1)
            i = len(text) if end < 0 else end
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start : i + 1]
        i += 1
    return text[start:]


def _bicep_value(block: str, name: str) -> str | None:
    """A property's literal string value in *block*; ``"${...}"`` for a non-literal one."""
    m = _BICEP_PROPERTY_RE[name].search(block)
    if m is None:
        return None
    return m.group(1) if m.group(1) is not None else "${" + m.group(2).strip() + "}"


def _bicep(text: str) -> Iterator[Claim]:
    for m in _BICEP_API_RE.finditer(text):
        block = _block(text, m.end() - 1)
        path = _bicep_value(block, "path")
        if path is None or "${" in path:
            continue
        claim = _route(path, _bicep_value(block, "serviceUrl"))
        if claim is not None:
            yield claim


def _string(node: Any, name: str) -> str | None:
    value = field(node, name)
    return value if isinstance(value, str) else None


def _arm_apis(node: Any) -> Iterator[dict]:
    """Every APIM API resource in an ARM template, nested ``resources`` included."""
    for resource in field(node, "resources") or ():
        if not isinstance(resource, dict):
            continue
        if str(field(resource, "type") or "").lower() == _API_TYPE:
            yield resource
        yield from _arm_apis(resource)


def _json(file: SourceFile) -> Iterator[Claim]:
    config = load_json_config(file.text)
    if file.path.name.lower() == "apiinformation.json":
        apis: Iterable[Any] = [config]
    else:
        apis = _arm_apis(config)
    for api in apis:
        properties = field(api, "properties")
        path = _string(properties, "path")
        if path is None or path.startswith("["):
            continue
        claim = _route(path, _string(properties, "serviceurl"))
        if claim is not None:
            yield claim


def read(file: SourceFile) -> Iterable[Claim]:
    if file.path.suffix.lower() == ".bicep":
        yield from _bicep(file.text)
    else:
        yield from _json(file)


SOURCE = ServiceNameSource(
    name="apim",
    filenames=("*.bicep", "*.json"),
    marker="serviceUrl",
    read=read,
)
