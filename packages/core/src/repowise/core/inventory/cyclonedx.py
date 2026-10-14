"""Package usage as a CycloneDX 1.6 JSON document.

One ``library`` component per package a manifest declares, with
``evidence.occurrences`` listing the files that import it (``location``,
``line``, ``symbol``, ``additionalContext``: the keys the 1.6 schema allows on
an occurrence). This records where packages are used. It is not an SBOM: no
hashes, no licenses, no transitive resolution, and no version beyond the
specifier a manifest declares, which is kept as a property rather than as
``version`` because a range is not a resolved version.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote

from repowise.core.inventory.usage import (
    dist_key,
    load_usage_index,
    lookup_key,
    package_usages,
)

SPEC_VERSION = "1.6"
_PURL_TYPE = {
    "npm": "npm",
    "pypi": "pypi",
    "cargo": "cargo",
    "go": "golang",
    "nuget": "nuget",
    "maven": "maven",
}


def purl(ecosystem: str, name: str) -> str | None:
    """A versionless package URL, or ``None`` for an ecosystem purl has no type for."""
    kind = _PURL_TYPE.get(ecosystem)
    if kind is None:
        return None
    if ecosystem == "pypi":
        name = dist_key(name)
    if ecosystem == "maven":
        group, sep, artifact = name.partition(":")
        if not sep or not group or not artifact:
            return None  # a purl names a Maven package by group and artifact
        return f"pkg:maven/{quote(group)}/{quote(artifact)}"
    return f"pkg:{kind}/" + "/".join(quote(part) for part in name.split("/"))


def _occurrence(site: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {"location": site["path"]}
    if "line" in site:
        out["line"] = site["line"]
    if site["symbols"]:
        out["symbol"] = ", ".join(site["symbols"])
    if "statement" in site:
        out["additionalContext"] = site["statement"]
    return out


def _component(ecosystem: str, name: str, doc: dict[str, Any]) -> dict[str, Any]:
    # One component per package however its manifests spell it (``PyYAML``,
    # ``pyyaml``): the bom-ref uses the same normalised name the lookup does.
    ref = f"{ecosystem}:{lookup_key(ecosystem, name)}"
    comp: dict[str, Any] = {"type": "library", "bom-ref": ref, "name": name}
    if (url := purl(ecosystem, name)) is not None:
        comp["purl"] = url
    props = [{"name": "repowise:ecosystem", "value": ecosystem}]
    seen: set[tuple[str, str]] = set()
    for decl in doc["declared"]:
        if not decl["package_key"].startswith(f"{ecosystem}:"):
            continue
        for key in ("declared_in", "version"):
            value = decl[key]
            if value and (key, value) not in seen:
                seen.add((key, value))
                label = "declared_in" if key == "declared_in" else "declared_version"
                props.append({"name": f"repowise:{label}", "value": value})
    states = doc["summary"]["reachability"]
    props.extend(
        {"name": f"repowise:importing_files_{state}", "value": str(n)}
        for state, n in states.items()
        if n
    )
    comp["properties"] = props
    # Only declared links: an occurrence matched by import name alone is a guess.
    sites = [s for s in doc["sites"] if s["mapping"] == "declared"]
    if sites:
        comp["evidence"] = {"occurrences": [_occurrence(s) for s in sites]}
    return comp


async def cyclonedx_bom(
    session: Any, repository_id: str, repo_root: Path | str, repo_name: str
) -> dict[str, Any]:
    """The CycloneDX 1.6 document for one indexed repository."""
    from repowise.core import __version__

    index = await load_usage_index(session, repository_id)
    names: dict[tuple[str, str], str] = {}
    for d in sorted(index.declarations, key=lambda d: d.name):
        names.setdefault((d.ecosystem, lookup_key(d.ecosystem, d.name)), d.name)
    keys = sorted((eco, name) for (eco, _key), name in names.items())
    docs = await package_usages(
        session, repository_id, repo_root, [(name, eco) for eco, name in keys], index=index
    )
    return {
        "bomFormat": "CycloneDX",
        "specVersion": SPEC_VERSION,
        "serialNumber": uuid.uuid4().urn,
        "version": 1,
        "metadata": {
            "timestamp": datetime.now(UTC).replace(microsecond=0).isoformat(),
            "tools": {
                "components": [{"type": "application", "name": "repowise", "version": __version__}]
            },
            "component": {"type": "application", "bom-ref": "repository", "name": repo_name},
            "properties": [
                {
                    "name": "repowise:scope",
                    "value": "declared packages and where the import graph uses them; "
                    "not an SBOM (no hashes, no transitive resolution, no resolved versions)",
                }
            ],
        },
        "components": [
            _component(eco, name, doc) for (eco, name), doc in zip(keys, docs, strict=True)
        ],
    }


__all__ = ["SPEC_VERSION", "cyclonedx_bom", "purl"]
