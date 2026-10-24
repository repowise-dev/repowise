"""docker-compose: a service is the repo its build context (or image) names.

``services.catalogue.build.context: ../catalogue`` puts the name ``catalogue``
in the repo at that path; a ``dockerfile`` narrows it to the Dockerfile's
directory. A service with no build names a repo only through its image's
basename (``org/cart:1.2`` -> ``cart``). ``container_name`` and ``hostname``
are the same container under another name. Every ``environment`` value that
reads as an address (``CATALOGUE_URL: http://catalogue:8080``) makes its key a
reference to that host.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import Any

from .base import ENV, Claim, ServiceNameSource, SourceFile, host_of, join_config_path
from .yaml_docs import load_yaml_docs

_PLACEHOLDER_RE = re.compile(r"\$\{[^}]*\}")


def _image_basename(image: str) -> str | None:
    name = _PLACEHOLDER_RE.sub("", image).rsplit("/", 1)[-1]
    name = name.split("@", 1)[0].split(":", 1)[0].strip()
    return name or None


def _build_dir(build: Any, base: Path) -> Path | None:
    if isinstance(build, str):
        context, dockerfile = build, None
    elif isinstance(build, dict):
        context, dockerfile = build.get("context", "."), build.get("dockerfile")
    else:
        return None
    if not isinstance(context, str) or "$" in context or "://" in context or context.startswith("git@"):
        return None
    where = join_config_path(base, context)
    if isinstance(dockerfile, str) and "$" not in dockerfile and "/" in dockerfile.replace("\\", "/"):
        where = join_config_path(where, dockerfile).parent
    return where


def _environment(env: Any) -> Iterator[tuple[str, str]]:
    if isinstance(env, dict):
        for key, value in env.items():
            if isinstance(value, str):
                yield str(key), value
    elif isinstance(env, list):
        for item in env:
            if isinstance(item, str) and "=" in item:
                key, value = item.split("=", 1)
                yield key, value


def _service_claims(svc: str, conf: dict[str, Any], base: Path) -> Iterator[Claim]:
    names = {svc.lower()}
    for key in ("container_name", "hostname"):
        value = conf.get(key)
        # A dotted hostname is a public name the container answers to, not a service name.
        if isinstance(value, str) and "$" not in value and "." not in value:
            names.add(value.lower())
    at = _build_dir(conf["build"], base) if "build" in conf else None
    image = conf.get("image")
    aliases = () if at is not None or not isinstance(image, str) else (_image_basename(image),)
    aliases = tuple(a for a in aliases if a)
    if at is not None or aliases:
        for name in sorted(names):
            yield Claim(name=name, at=at, aliases=aliases)
    for key, value in _environment(conf.get("environment")):
        host = host_of(key, value)
        if host:
            yield Claim(name=key.lower(), kind=ENV, ref=host)


def read(file: SourceFile) -> Iterable[Claim]:
    for doc in load_yaml_docs(file.text):
        services = doc.get("services") if isinstance(doc, dict) else None
        if not isinstance(services, dict):
            continue
        for svc, conf in services.items():
            if isinstance(svc, str) and isinstance(conf, dict):
                yield from _service_claims(svc, conf, file.path.parent)


SOURCE = ServiceNameSource(
    name="compose",
    filenames=("docker-compose*.yml", "docker-compose*.yaml", "compose*.yml", "compose*.yaml"),
    marker="services",
    read=read,
)
