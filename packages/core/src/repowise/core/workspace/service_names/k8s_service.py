"""Kubernetes ``Service`` manifests: the DNS name a cluster gives a workload.

A Service's ``metadata.name`` is the host its callers use. It names a repo
through a selector or label value equal to a repo alias (``app: catalogue``);
otherwise it is the repo holding the manifest, but only when that repo
declares no other Service, since a deploy repo lists every service. Helm
templates do not parse as YAML and are skipped; their rendered names are not
known without the chart's values.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from .base import Claim, ServiceNameSource, SourceFile
from .yaml_docs import load_yaml_docs

_LABEL_KEYS = ("app", "app.kubernetes.io/name")


def _labels(*maps: Any) -> tuple[str, ...]:
    out: list[str] = []
    for m in maps:
        if isinstance(m, dict):
            out.extend(str(m[k]).lower() for k in _LABEL_KEYS if isinstance(m.get(k), str))
    return tuple(dict.fromkeys(out))


def read(file: SourceFile) -> Iterable[Claim]:
    if "{{" in file.text:
        return
    for doc in load_yaml_docs(file.text):
        if not isinstance(doc, dict) or doc.get("kind") != "Service":
            continue
        meta = doc.get("metadata") or {}
        name = meta.get("name") if isinstance(meta, dict) else None
        if not isinstance(name, str) or not name:
            continue
        spec = doc.get("spec") or {}
        if isinstance(spec, dict) and spec.get("type") == "ExternalName":
            continue  # an alias for a host outside the cluster, served by no repo here
        selector = spec.get("selector") if isinstance(spec, dict) else None
        yield Claim(
            name=name.lower(),
            aliases=_labels(selector, meta.get("labels")),
            own_repo=True,
        )


SOURCE = ServiceNameSource(
    name="k8s_service",
    filenames=("*.yaml", "*.yml"),
    under=frozenset(
        {"k8s", "kubernetes", "kube", "manifests", "deploy", "deployment", "deployments", "kustomize"}
    ),
    marker="kind: Service",
    read=read,
)
