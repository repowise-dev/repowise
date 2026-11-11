"""Package lists and advisories from a file the user supplies: the ``advisory-source`` seam.

The local default, ``file``, reads one JSON document and returns the packages it
names. Four shapes are recognised:

* Syft JSON (``artifacts[]``: name, version, purl);
* SPDX 2.x JSON (``packages[]``: name, versionInfo, a purl external ref);
* CycloneDX JSON (``components[]``: name, version, purl);
* OSV JSON: one record, a list of records, ``{"vulns": [...]}``, or OSV-Scanner
  output (``results[].packages[]``). Each record's ``affected[].package`` is a
  package, carrying the record's id and aliases.

Nothing here fetches a feed or decides whether a version is affected. An OSV
file's affected list is the user's own data, joined to usage by package name
only; its version ranges are not evaluated. A plugin registers another source
(a hosted advisory feed) under ``SeamRegistry("advisory-source")``, selected
with ``REPOWISE_SEAM_ADVISORY_SOURCE``; it receives the repository's declared
packages so it can look them up.
"""

from __future__ import annotations

import json
from collections import deque
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import unquote

from repowise.core.registry.plugins import SeamRegistry

#: Larger inputs are refused rather than parsed into memory.
MAX_FILE_BYTES = 200_000_000

#: OSV ecosystem names and purl types to the manifest ecosystems the index uses.
_ECOSYSTEMS = {
    "pypi": "pypi",
    "npm": "npm",
    "crates.io": "cargo",
    "cargo": "cargo",
    "go": "go",
    "golang": "go",
    "nuget": "nuget",
    "maven": "maven",
    "packagist": "composer",
    "composer": "composer",
}


@dataclass(frozen=True)
class Advisory:
    id: str
    aliases: tuple[str, ...] = ()
    summary: str | None = None


@dataclass(frozen=True)
class PackageMention:
    """One package a supplied document names, as the document names it."""

    name: str
    ecosystem: str | None = None
    version: str | None = None
    advisories: tuple[Advisory, ...] = ()


@dataclass(frozen=True)
class SourceDocument:
    format: str  # "syft" | "spdx" | "cyclonedx" | "osv"
    mentions: tuple[PackageMention, ...]


class AdvisorySource(Protocol):
    def read(self, location: str, *, packages: Sequence[tuple[str, str]] = ()) -> SourceDocument:
        """The packages (and advisories) at *location*. *packages* are the
        repository's declared ``(ecosystem, name)`` pairs, for a source that looks
        them up; a file source ignores them."""
        ...


def _ecosystem(raw: Any) -> str | None:
    if not isinstance(raw, str) or not raw:
        return None
    return _ECOSYSTEMS.get(raw.split(":", 1)[0].casefold())


def from_purl(purl: str) -> tuple[str | None, str | None, str | None]:
    """``(ecosystem, name, version)`` from a package URL; Nones where it has none."""
    if not isinstance(purl, str) or not purl.startswith("pkg:"):
        return None, None, None
    body = purl[4:].split("#", 1)[0].split("?", 1)[0]
    kind, _, rest = body.partition("/")
    # The version follows the last "@" after the last "/": a raw npm scope
    # (``@scope/pkg@1.0``) keeps its own "@".
    at = rest.rfind("@", rest.rfind("/") + 1)
    path, version = (rest[:at], rest[at + 1 :]) if at > 0 else (rest, "")
    parts = [unquote(p) for p in path.split("/") if p]
    if not parts:
        return None, None, None
    eco = _ecosystem(kind)
    if eco == "maven" and len(parts) >= 2:
        name = f"{parts[-2]}:{parts[-1]}"
    elif eco == "composer" or eco == "npm" or eco == "go":
        name = "/".join(parts)
    else:
        name = parts[-1]
    return eco, name, unquote(version) or None


def _mention(name: Any, version: Any, purl: Any, eco: Any = None) -> PackageMention | None:
    p_eco, p_name, p_version = from_purl(purl) if isinstance(purl, str) else (None, None, None)
    name = p_name or name
    if not isinstance(name, str) or not name.strip():
        return None
    version = version if isinstance(version, str) and version else p_version
    return PackageMention(name.strip(), p_eco or _ecosystem(eco), version)


def _syft(doc: dict) -> list[PackageMention]:
    return [
        m
        for a in doc.get("artifacts") or []
        if isinstance(a, dict) and (m := _mention(a.get("name"), a.get("version"), a.get("purl")))
    ]


def _spdx(doc: dict) -> list[PackageMention]:
    out = []
    for pkg in doc.get("packages") or []:
        if not isinstance(pkg, dict):
            continue
        purl = next(
            (
                ref.get("referenceLocator")
                for ref in pkg.get("externalRefs") or []
                if isinstance(ref, dict) and ref.get("referenceType") == "purl"
            ),
            None,
        )
        if (m := _mention(pkg.get("name"), pkg.get("versionInfo"), purl)) is not None:
            out.append(m)
    return out


def _cyclonedx(doc: dict) -> list[PackageMention]:
    out: list[PackageMention] = []
    stack = deque(doc.get("components") or [])
    while stack:
        comp = stack.popleft()
        if not isinstance(comp, dict):
            continue
        stack.extend(comp.get("components") or [])
        if (m := _mention(comp.get("name"), comp.get("version"), comp.get("purl"))) is not None:
            out.append(m)
    return out


def _advisory(rec: dict) -> Advisory | None:
    if not isinstance(rec.get("id"), str):
        return None
    return Advisory(
        rec["id"],
        tuple(a for a in rec.get("aliases") or [] if isinstance(a, str)),
        rec["summary"] if isinstance(rec.get("summary"), str) else None,
    )


def _osv_record(rec: dict) -> list[PackageMention]:
    advisory = _advisory(rec)
    if advisory is None:
        return []
    out = []
    for aff in rec.get("affected") or []:
        pkg = aff.get("package") if isinstance(aff, dict) else None
        if not isinstance(pkg, dict):
            continue
        m = _mention(pkg.get("name"), None, pkg.get("purl"), pkg.get("ecosystem"))
        if m is not None:
            out.append(PackageMention(m.name, m.ecosystem, None, (advisory,)))
    return out


def _osv_scanner(doc: dict) -> list[PackageMention]:
    out = []
    for result in doc.get("results") or []:
        for entry in (result.get("packages") or []) if isinstance(result, dict) else []:
            if not isinstance(entry, dict) or not isinstance(entry.get("package"), dict):
                continue
            pkg = entry["package"]
            m = _mention(pkg.get("name"), pkg.get("version"), pkg.get("purl"), pkg.get("ecosystem"))
            if m is None:
                continue
            advisories = tuple(
                a
                for v in entry.get("vulnerabilities") or []
                if isinstance(v, dict) and (a := _advisory(v)) is not None
            )
            out.append(PackageMention(m.name, m.ecosystem, m.version, advisories))
    return out


def parse_document(data: Any) -> SourceDocument:
    """Recognise and read one decoded document; ``ValueError`` when it is none of the four."""
    if isinstance(data, list):
        return SourceDocument("osv", tuple(m for r in data if isinstance(r, dict) for m in _osv_record(r)))
    if not isinstance(data, dict):
        raise ValueError("not a Syft, SPDX, CycloneDX or OSV JSON document")
    if "artifacts" in data and ("descriptor" in data or "source" in data):
        return SourceDocument("syft", tuple(_syft(data)))
    if str(data.get("spdxVersion", "")).startswith("SPDX-"):
        return SourceDocument("spdx", tuple(_spdx(data)))
    if data.get("bomFormat") == "CycloneDX":
        return SourceDocument("cyclonedx", tuple(_cyclonedx(data)))
    if isinstance(data.get("results"), list):
        return SourceDocument("osv", tuple(_osv_scanner(data)))
    if isinstance(data.get("vulns"), list):
        records = [r for r in data["vulns"] if isinstance(r, dict)]
        return SourceDocument("osv", tuple(m for r in records for m in _osv_record(r)))
    if isinstance(data.get("id"), str) and isinstance(data.get("affected"), list):
        return SourceDocument("osv", tuple(_osv_record(data)))
    raise ValueError("not a Syft, SPDX, CycloneDX or OSV JSON document")


class FileAdvisorySource:
    """Reads one local JSON file; never touches the network."""

    def read(self, location: str, *, packages: Sequence[tuple[str, str]] = ()) -> SourceDocument:
        path = Path(location)
        try:
            if path.stat().st_size > MAX_FILE_BYTES:
                raise ValueError(f"{location}: larger than {MAX_FILE_BYTES} bytes; not read")
            data = json.loads(path.read_text(encoding="utf-8"))
        except OSError as exc:
            raise ValueError(f"{location}: cannot be read ({exc.strerror or exc})") from exc
        except RecursionError as exc:
            raise ValueError(f"{location}: JSON nested too deeply to read") from exc
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError(f"{location}: not valid UTF-8 JSON") from exc
        try:
            return parse_document(data)
        except RecursionError as exc:
            raise ValueError(f"{location}: document nested too deeply to read") from exc


def _merge(mentions: Sequence[PackageMention]) -> dict[tuple[str | None, str], dict[str, Any]]:
    merged: dict[tuple[str | None, str], dict[str, Any]] = {}
    for m in mentions:
        row = merged.setdefault((m.ecosystem, m.name), {"versions": [], "advisories": {}})
        if m.version and m.version not in row["versions"]:
            row["versions"].append(m.version)
        for a in m.advisories:
            row["advisories"].setdefault(a.id, a)
    return merged


async def join_usage(
    session: Any,
    repository_id: str,
    repo_root: Path | str,
    location: str,
    *,
    source: str | None = None,
) -> dict[str, Any]:
    """Read *location* through the selected advisory source and join its packages
    to where this repository uses them, by name. ``ValueError`` on an unreadable
    document."""
    from repowise.core.inventory.usage import load_usage_index, package_usages

    index = await load_usage_index(session, repository_id)
    declared = sorted({(d.ecosystem, d.name) for d in index.declarations})
    document = advisory_sources.get(source).read(location, packages=declared)
    merged = _merge(document.mentions)
    keys = list(merged)
    docs = await package_usages(
        session, repository_id, repo_root, [(n, e) for e, n in keys], index=index
    )
    matched: list[dict[str, Any]] = []
    not_found: list[dict[str, Any]] = []
    for (eco, name), doc in zip(keys, docs, strict=True):
        info = merged[(eco, name)]
        row: dict[str, Any] = {"name": name, "ecosystem": eco, "versions": info["versions"]}
        if info["advisories"]:
            row["advisories"] = [
                {"id": a.id, "aliases": list(a.aliases), **({"summary": a.summary} if a.summary else {})}
                for a in info["advisories"].values()
            ]
        if not doc["declared"] and not doc["sites"]:
            not_found.append(row)
            continue
        row.update(
            declared=bool(doc["declared"]),
            summary=doc["summary"],
            components=[c["name"] for c in doc["components"]],
            sites=[
                {
                    k: site[k]
                    for k in ("path", "line", "component", "mapping", "reachability")
                    if k in site
                }
                for site in doc["sites"]
            ],
        )
        matched.append(row)

    def order(row: dict[str, Any]) -> tuple:
        reach = row["summary"]["reachability"]
        return (not row.get("advisories"), -reach["reachable"], -row["summary"]["files"], row["name"])

    matched.sort(key=order)
    return {
        "format": document.format,
        "mentions": len(keys),
        "matched": matched,
        "not_found": len(not_found),
        # Only the ones carrying advisories: the rest are usually transitive.
        "not_found_with_advisories": [r for r in not_found if r.get("advisories")],
        "method": "joined by package name (and ecosystem when the file gives one); "
        "advisory version ranges are not evaluated",
    }


advisory_sources: SeamRegistry[AdvisorySource] = SeamRegistry("advisory-source")
advisory_sources.register("file", FileAdvisorySource(), default=True)


__all__ = [
    "Advisory",
    "AdvisorySource",
    "FileAdvisorySource",
    "PackageMention",
    "SourceDocument",
    "advisory_sources",
    "from_purl",
    "join_usage",
    "parse_document",
]
