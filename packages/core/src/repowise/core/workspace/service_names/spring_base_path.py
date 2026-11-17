"""Spring's server base path: the prefix a module serves every route under.

``server.servlet.context-path`` (Boot 2+), ``server.context-path`` (Boot 1) and
``spring.webflux.base-path``, in YAML or ``.properties``, any relaxed spelling
(``contextPath``, ``context_path``).

Two places say it. A module's own ``application`` or ``bootstrap`` file in its
``src/main/resources`` (or its ``config/`` child) applies to that module. A
config server's ``<app>.yml`` (``shared/account-service.yml``) applies to the
service the file is named after; the matcher places that name through the
service-name map. Not read: an ``application`` file anywhere else (a config
server's shared defaults would apply to every app at once) and a profile file
(``application-dev.yml``): it holds only while that profile is active.
A config server's ``<app>-<profile>.yml`` names no service, so it places nothing.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import Any

from .base import Claim, ServiceNameSource, SourceFile
from .yaml_docs import load_yaml_docs

# Relaxed binding: dashes and underscores dropped, lowercased.
_KEYS = frozenset({"server.servlet.contextpath", "server.contextpath", "spring.webflux.basepath"})
# Cheap check before a YAML parse: most gated files say none of these.
_HINT_RE = re.compile(r"context[-_]?path|base[-_]?path", re.IGNORECASE)
_PROPERTY_RE = re.compile(r"^\s*([A-Za-z][\w.\-]*)\s*[=:]\s*(\S*)\s*$", re.MULTILINE)
_OWN_NAMES = frozenset({"application", "bootstrap"})
# A document scoped to a profile (Boot 2.4+ and the legacy spelling).
_PROFILE_KEYS = frozenset({"spring.config.activate.onprofile", "spring.profiles"})
# The document separator of a multi-document ``.properties`` file.
_PROPERTIES_DOC_RE = re.compile(r"^[#!]---\s*$", re.MULTILINE)


def _relaxed(key: str) -> str:
    return key.replace("-", "").replace("_", "").lower()


def _flat(node: Any, key: str = "") -> Iterator[tuple[str, Any]]:
    if isinstance(node, dict):
        for k, v in node.items():
            yield from _flat(v, f"{key}.{k}" if key else str(k))
    else:
        yield key, node


def _docs(file: SourceFile) -> list[list[tuple[str, Any]]]:
    """Each document's ``(key, value)`` pairs, a profile-scoped document left out:
    it holds only while that profile is active."""
    if file.path.suffix.lower() == ".properties":
        docs = [_PROPERTY_RE.findall(d) for d in _PROPERTIES_DOC_RE.split(file.text)]
    else:
        docs = [list(_flat(doc)) for doc in load_yaml_docs(file.text)]
    return [d for d in docs if not any(_relaxed(k) in _PROFILE_KEYS for k, _ in d)]


def _values(file: SourceFile) -> set[str]:
    out: set[str] = set()
    for key, value in (kv for doc in _docs(file) for kv in doc):
        if _relaxed(key) in _KEYS and isinstance(value, str):
            path = value.strip().strip("'\"").rstrip("/").lower()
            if path.startswith("/") and "$" not in path and "{" not in path:
                out.add(path)
    return out


def read(file: SourceFile) -> Iterable[Claim]:
    if not _HINT_RE.search(file.text):
        return
    stem = file.path.stem.lower()
    module = _own_module(file.path) if stem in _OWN_NAMES else None
    if stem.startswith(("application", "bootstrap")) and module is None:
        return  # shared defaults, a profile file, or a file no module loads
    values = _values(file)
    if len(values) != 1:  # profiles disagreeing: no one base path to trust
        return
    [path] = values
    if module is not None:
        yield Claim(name="", base_path=path, at=module)
    else:
        yield Claim(name=stem, base_path=path)


def _own_module(path: Path) -> Path | None:
    """The module whose ``src/main/resources[/config]`` holds *path*, else None."""
    d = path.parent
    if d.name.lower() == "config":
        d = d.parent
    parts = [p.lower() for p in d.parts[-3:]]
    return d.parent.parent.parent if parts == ["src", "main", "resources"] else None


SOURCE = ServiceNameSource(
    name="spring_base_path",
    filenames=("*.yml", "*.yaml", "*.properties"),
    # A module's resources, and where config servers keep their files. Ceiling:
    # a git-backed config repo with its files at the root is not read; adding
    # it is a gate on that repo's layout, not a glob.
    under=frozenset({"resources", "config", "configs", "shared"}),
    marker="path",
    read=read,
)
