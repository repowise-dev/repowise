"""Spring ``spring.application.name``: the name Eureka, Feign and ``lb://`` use.

Read from a module's ``application`` or ``bootstrap`` file (YAML or
properties); the name belongs to the module the file sits in. A config
server's ``<app>.yml`` is not read: it configures an app that lives elsewhere,
so it says nothing about which repo serves the name.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Any

from .base import Claim, ServiceNameSource, SourceFile
from .yaml_docs import load_yaml_docs

_PROPERTY_RE = re.compile(r"^\s*spring\.application\.name\s*[=:]\s*(\S+)\s*$", re.MULTILINE)


def _yaml_name(doc: Any) -> Any:
    if not isinstance(doc, dict):
        return None
    if "spring.application.name" in doc:
        return doc["spring.application.name"]
    node: Any = doc
    for key in ("spring", "application", "name"):
        node = node.get(key) if isinstance(node, dict) else None
    return node


def read(file: SourceFile) -> Iterable[Claim]:
    if file.path.suffix.lower() == ".properties":
        found = _PROPERTY_RE.findall(file.text)
    else:
        found = [_yaml_name(doc) for doc in load_yaml_docs(file.text)]
    names = {n.strip().lower() for n in found if isinstance(n, str) and n.strip() and "$" not in n}
    if len(names) == 1:  # profiles naming the app differently: no one name to trust
        yield Claim(name=names.pop(), at=file.path.parent)


SOURCE = ServiceNameSource(
    name="spring_application_name",
    filenames=(
        "application.yml",
        "application.yaml",
        "application.properties",
        "bootstrap.yml",
        "bootstrap.yaml",
        "bootstrap.properties",
    ),
    marker="name",
    read=read,
)
