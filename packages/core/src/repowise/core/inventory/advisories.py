"""Package lists and advisories from a file the user supplies: the ``advisory-source`` seam.

The local default, ``file``, reads one JSON document and returns the packages it
names. Four shapes are recognised:

* Syft JSON (``artifacts[]``: name, version, purl);
* SPDX 2.x JSON (``packages[]``: name, versionInfo, a purl external ref);
* CycloneDX JSON (``components[]``: name, version, purl);
* OSV JSON: one record, a list of records, ``{"vulns": [...]}``, or OSV-Scanner
  output (``results[].packages[]``). Each record's ``affected[].package`` is a
  package, carrying the record's id and aliases.

Each advisory keeps what the record says about the package: severity, the
affected package paths and symbols, the affected ranges and the fixed versions
(read by :mod:`repowise.core.inventory.osv`). OSV-Scanner output is already
version-resolved. A raw OSV record names no installed version, so the join
evaluates its ranges against the version the repository pins exactly
(:mod:`repowise.core.inventory.versions`), and leaves it unevaluated when the
manifest declares a range. Nothing here fetches a feed. A plugin registers another source
(a hosted advisory feed) under ``SeamRegistry("advisory-source")``, selected
with ``REPOWISE_SEAM_ADVISORY_SOURCE``; it receives the repository's declared
packages so it can look them up.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import deque
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import unquote

from repowise.core.inventory.osv import (
    AffectedImport,
    affected_imports,
    affected_ranges,
    affected_versions,
    cve_aliases,
    dedupe_by_cve,
    fixed_versions,
    label_for_score,
    pick_fixed,
    severity_from_osv,
)
from repowise.core.inventory.versions import affected_by, exact_version
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
    """One advisory as it applies to one package.

    *severity* is ``critical``, ``high``, ``medium`` or ``low`` (None when the
    record gives none); *score* the CVSS base score when one is known.
    *affected* holds the package paths and symbols the record names (empty: no
    symbol-level claim can be made). *ranges* and *versions* are the record's
    affected ranges and versions for this package, *fixed* every fixed version
    its ranges list.
    """

    id: str
    aliases: tuple[str, ...] = ()
    summary: str | None = None
    severity: str | None = None
    score: float | None = None
    affected: tuple[AffectedImport, ...] = ()
    fixed: tuple[str, ...] = ()
    ranges: tuple[tuple[tuple[str, str], ...], ...] = ()
    versions: tuple[str, ...] = ()

    @property
    def affected_symbols(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys(s for imp in self.affected for s in imp.symbols))

    @property
    def affected_paths(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys(imp.path for imp in self.affected if imp.path))

    def affects(self, ecosystem: str | None, version: str | None) -> bool | None:
        """Whether *version* is in this advisory's affected ranges; ``None`` when undecidable."""
        return affected_by(ecosystem, version, self.ranges, self.versions)


@dataclass(frozen=True)
class PackageMention:
    """One package a supplied document names, as the document names it.

    *direct* is True for a direct dependency, False for a transitive one, None
    when the document does not say (only a CycloneDX dependency graph does).
    """

    name: str
    ecosystem: str | None = None
    version: str | None = None
    advisories: tuple[Advisory, ...] = ()
    direct: bool | None = None


@dataclass(frozen=True)
class SourceDocument:
    format: str  # "syft" | "spdx" | "cyclonedx" | "osv"
    mentions: tuple[PackageMention, ...]
    #: SHA-256 of the file read, when the source read one.
    sha256: str | None = None


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


def cyclonedx_transitive(doc: dict) -> dict[str, bool]:
    """``bom-ref`` to True (transitive) or False (direct) from a CycloneDX dependency graph.

    Roots are refs with dependencies that nothing depends on, plus
    ``metadata.component``. Direct means a root depends on it; anything else
    depended on is transitive. A ref outside the graph is absent (unknown,
    never guessed), and a document with no graph gives ``{}``.
    """
    deps = [
        d
        for d in doc.get("dependencies") or []
        if isinstance(d, dict) and isinstance(d.get("ref"), str)
    ]
    by_ref = {d["ref"]: [c for c in d.get("dependsOn") or [] if isinstance(c, str)] for d in deps}
    depended_on = {child for children in by_ref.values() for child in children}
    roots = {ref for ref in by_ref if ref not in depended_on}
    meta = doc.get("metadata") if isinstance(doc.get("metadata"), dict) else {}
    component = meta.get("component") if isinstance(meta.get("component"), dict) else {}
    if component.get("bom-ref") in by_ref:
        roots.add(component["bom-ref"])
    direct = {child for root in roots for child in by_ref.get(root, [])}
    return {ref: ref not in direct for ref in direct | depended_on}


def _cyclonedx(doc: dict) -> list[PackageMention]:
    transitive = cyclonedx_transitive(doc)
    out: list[PackageMention] = []
    stack = deque(doc.get("components") or [])
    while stack:
        comp = stack.popleft()
        if not isinstance(comp, dict):
            continue
        stack.extend(comp.get("components") or [])
        if (m := _mention(comp.get("name"), comp.get("version"), comp.get("purl"))) is not None:
            ref = comp.get("bom-ref")
            if isinstance(ref, str) and ref in transitive:
                m = PackageMention(m.name, m.ecosystem, m.version, direct=not transitive[ref])
            out.append(m)
    return out


def _canonical(ecosystem: str | None, name: str) -> str:
    # PEP 503 for PyPI (``Foo_Bar`` is ``foo-bar``); case-insensitive elsewhere.
    return re.sub(r"[-_.]+", "-", name).lower() if ecosystem == "pypi" else name.casefold()


def _entries_for(rec: dict, ecosystem: str | None, name: str) -> list[dict]:
    """The record's ``affected[]`` entries naming the package ``(ecosystem, name)``."""
    want = _canonical(ecosystem, name)
    out = []
    for aff in rec.get("affected") or []:
        pkg = aff.get("package") if isinstance(aff, dict) else None
        if not isinstance(pkg, dict):
            continue
        m = _mention(pkg.get("name"), None, pkg.get("purl"), pkg.get("ecosystem"))
        if m is None or _canonical(m.ecosystem or ecosystem, m.name) != want:
            continue
        if ecosystem and m.ecosystem and m.ecosystem != ecosystem:
            continue
        out.append(aff)
    return out


def _advisory(rec: dict, entries: Sequence[dict] = (), max_severity: Any = None) -> Advisory | None:
    """*rec* as it applies to the package its *entries* describe.

    *max_severity* is OSV-Scanner's group score, used when no vector or label parses.
    """
    if not isinstance(rec.get("id"), str) or not rec["id"]:
        return None
    score, label = severity_from_osv(rec)
    if score is None and label == "unknown" and isinstance(max_severity, (str, int, float)):
        try:
            score = float(max_severity)
        except ValueError:
            score = None
        if score is not None and 0.0 < score <= 10.0:
            label = label_for_score(score)
        else:
            score = None
    ranges = tuple(r for e in entries for r in affected_ranges(e))
    return Advisory(
        rec["id"],
        tuple(a for a in rec.get("aliases") or [] if isinstance(a, str)),
        rec["summary"] if isinstance(rec.get("summary"), str) else None,
        severity=None if label == "unknown" else label,
        score=score,
        affected=tuple(dict.fromkeys(i for e in entries for i in affected_imports(e))),
        fixed=fixed_versions(ranges),
        ranges=ranges,
        versions=tuple(dict.fromkeys(v for e in entries for v in affected_versions(e))),
    )


def _osv_record(rec: dict) -> list[PackageMention]:
    if not isinstance(rec.get("id"), str) or not rec["id"]:
        return []
    # One mention per package, however many affected[] entries name it.
    packages: dict[tuple[str | None, str], PackageMention] = {}
    for aff in rec.get("affected") or []:
        pkg = aff.get("package") if isinstance(aff, dict) else None
        if not isinstance(pkg, dict):
            continue
        m = _mention(pkg.get("name"), None, pkg.get("purl"), pkg.get("ecosystem"))
        if m is not None:
            packages.setdefault((m.ecosystem, _canonical(m.ecosystem, m.name)), m)
    out = []
    for m in packages.values():
        advisory = _advisory(rec, _entries_for(rec, m.ecosystem, m.name))
        if advisory is not None:
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
            group_score = {
                i: g.get("max_severity")
                for g in entry.get("groups") or []
                if isinstance(g, dict)
                for i in g.get("ids") or []
                if isinstance(i, str)
            }
            advisories = []
            for v in entry.get("vulnerabilities") or []:
                if not isinstance(v, dict):
                    continue
                a = _advisory(v, _entries_for(v, m.ecosystem, m.name), group_score.get(v.get("id")))
                if a is not None:
                    advisories.append(a)
            out.append(PackageMention(m.name, m.ecosystem, m.version, tuple(advisories)))
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
            raw = path.read_bytes()
            data = json.loads(raw.decode("utf-8"))
        except OSError as exc:
            raise ValueError(f"{location}: cannot be read ({exc.strerror or exc})") from exc
        except RecursionError as exc:
            raise ValueError(f"{location}: JSON nested too deeply to read") from exc
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError(f"{location}: not valid UTF-8 JSON") from exc
        try:
            document = parse_document(data)
        except RecursionError as exc:
            raise ValueError(f"{location}: document nested too deeply to read") from exc
        return SourceDocument(document.format, document.mentions, hashlib.sha256(raw).hexdigest())


@dataclass(frozen=True)
class PackageAdvisories:
    """One package a document names, its versions there, and its advisories (one per CVE)."""

    ecosystem: str | None
    name: str
    versions: tuple[str, ...] = ()
    advisories: tuple[Advisory, ...] = ()
    #: As :attr:`PackageMention.direct`; direct anywhere in the document is direct.
    direct: bool | None = None


@dataclass(frozen=True)
class StoredAdvisories:
    """The last advisory document imported for a repository (``package_advisories``).

    *sha256* is the hash of the file it was read from, so a reader can tell
    whether that file has changed since.
    """

    format: str
    sha256: str | None
    source: str | None
    imported_at: datetime
    packages: tuple[PackageAdvisories, ...]

    def get(self, ecosystem: str | None, name: str) -> PackageAdvisories | None:
        """The package's entry, matching the name as the document's ecosystem spells it."""
        want = _canonical(ecosystem, name)
        return next(
            (
                p
                for p in self.packages
                if _canonical(p.ecosystem or ecosystem, p.name) == want
                and (ecosystem is None or p.ecosystem in (None, ecosystem))
            ),
            None,
        )


def _direct(seen: Sequence[bool | None]) -> bool | None:
    # Direct anywhere is direct; transitive only when every mention says so.
    if True in seen:
        return True
    return False if seen and all(d is False for d in seen) else None


def _advisory_cves(a: Advisory) -> list[str]:
    return cve_aliases({"id": a.id, "aliases": list(a.aliases)})


def merge_mentions(mentions: Sequence[PackageMention]) -> tuple[PackageAdvisories, ...]:
    """One entry per ``(ecosystem, name)``: its versions, and its advisories with
    records describing the same CVE collapsed to one (scored first, then GHSA)."""
    merged: dict[tuple[str | None, str], dict[str, Any]] = {}
    for m in mentions:
        row = merged.setdefault(
            (m.ecosystem, m.name), {"versions": [], "advisories": {}, "direct": []}
        )
        if m.version and m.version not in row["versions"]:
            row["versions"].append(m.version)
        for a in m.advisories:
            row["advisories"].setdefault(a.id, a)
        row["direct"].append(m.direct)
    return tuple(
        PackageAdvisories(
            eco,
            name,
            tuple(row["versions"]),
            tuple(
                dedupe_by_cve(
                    row["advisories"].values(),
                    _advisory_cves,
                    lambda a: (a.score is not None, a.severity is not None, a.id.startswith("GHSA-")),
                )
            ),
            _direct(row["direct"]),
        )
        for (eco, name), row in merged.items()
    )


def _verdict(
    advisory: Advisory, ecosystem: str | None, source_versions: Sequence[str], declared: Sequence[dict]
) -> tuple[bool | None, str | None, str]:
    """``(affected, version, basis)`` for one advisory on one package.

    A document that names the package's version (OSV-Scanner) already matched
    the advisory to it, so it stands. Otherwise the advisory's ranges are
    evaluated against each version the manifests pin exactly; one declaration
    with a range spec leaves it undecided unless another pin is affected.
    """
    if source_versions:
        # Several versions: the document does not say which one the advisory hit.
        return True, source_versions[0] if len(source_versions) == 1 else None, "document"
    if not declared:
        return None, None, "no_version"
    verdicts: list[tuple[bool | None, str | None]] = []
    for d in declared:
        eco = ecosystem or str(d["package_key"]).split(":", 1)[0]
        pinned = exact_version(eco, d["version"])
        verdicts.append((advisory.affects(eco, pinned) if pinned else None, pinned))
    for state in (True, None):
        hit = next((v for v in verdicts if v[0] is state), None)
        if hit is not None:
            return state, hit[1], "declared_pin" if hit[1] else "declared_range"
    return False, verdicts[0][1], "declared_pin"


def _advisory_row(
    advisory: Advisory, ecosystem: str | None, affected: bool | None, version: str | None, basis: str
) -> dict[str, Any]:
    row: dict[str, Any] = {"id": advisory.id, "aliases": list(advisory.aliases)}
    if advisory.summary:
        row["summary"] = advisory.summary
    if advisory.severity:
        row["severity"] = advisory.severity
    if advisory.score is not None:
        row["score"] = advisory.score
    if advisory.fixed:
        row["fixed"] = list(advisory.fixed)
        if version and (fix := pick_fixed(ecosystem, advisory.fixed, version)):
            # The smallest fix above the version in use.
            row["fix"] = fix
    if advisory.affected:
        row["affected_symbols"] = list(advisory.affected_symbols)
        if advisory.affected_paths:
            row["affected_paths"] = list(advisory.affected_paths)
    row.update(affected=affected, basis=basis)
    if version:
        row["version"] = version
    return row


async def join_usage(
    session: Any,
    repository_id: str,
    repo_root: Path | str,
    location: str,
    *,
    source: str | None = None,
    store: bool = False,
) -> dict[str, Any]:
    """Read *location* through the selected advisory source and join its packages
    to where this repository uses them, by name. With *store*, the document
    replaces the repository's stored one (:func:`load_advisories` reads it
    back). ``ValueError`` on an unreadable document."""
    from repowise.core.inventory.usage import load_usage_index, package_usages

    index = await load_usage_index(session, repository_id)
    declared = sorted({(d.ecosystem, d.name) for d in index.declarations})
    document = advisory_sources.get(source).read(location, packages=declared)
    packages = merge_mentions(document.mentions)
    if store:
        from repowise.core.persistence.crud.package_advisories import replace_package_advisories

        await replace_package_advisories(
            session, repository_id, packages,
            format=document.format, sha256=document.sha256, source=location,
        )
    docs = await package_usages(
        session, repository_id, repo_root, [(p.name, p.ecosystem) for p in packages], index=index
    )
    matched: list[dict[str, Any]] = []
    not_found: list[dict[str, Any]] = []
    for pkg, doc in zip(packages, docs, strict=True):
        row: dict[str, Any] = {"name": pkg.name, "ecosystem": pkg.ecosystem, "versions": list(pkg.versions)}
        if pkg.direct is not None:
            row["direct"] = pkg.direct
        advisories, clear = [], []
        for a in pkg.advisories:
            affected, version, basis = _verdict(a, pkg.ecosystem, pkg.versions, doc["declared"])
            if affected is False:
                clear.append({"id": a.id, "version": version})
            else:
                advisories.append(_advisory_row(a, pkg.ecosystem, affected, version, basis))
        if advisories:
            row["advisories"] = advisories
        if clear:
            # Proven outside every affected range of the advisory.
            row["not_affected"] = clear
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
        "mentions": len(packages),
        "matched": matched,
        "not_found": len(not_found),
        # Only the ones carrying advisories: the rest are usually transitive.
        "not_found_with_advisories": [r for r in not_found if r.get("advisories")],
        "method": "joined by package name (and ecosystem when the file gives one); "
        "a document naming no version is checked against each exact version the "
        "manifests pin, and stays undecided (affected: null) for a range spec",
        **({"stored": True, "sha256": document.sha256} if store else {}),
    }


async def load_advisories(
    session: Any,
    repository_id: str,
    *,
    packages: Sequence[tuple[str | None, str]] | None = None,
) -> StoredAdvisories | None:
    """The repository's last stored advisory document, or ``None`` when none was stored.

    *packages* (``(ecosystem, name)`` pairs) narrows it to those packages.
    """
    from repowise.core.persistence.crud.package_advisories import load_package_advisories

    return await load_package_advisories(session, repository_id, packages=packages)


advisory_sources: SeamRegistry[AdvisorySource] = SeamRegistry("advisory-source")
advisory_sources.register("file", FileAdvisorySource(), default=True)


__all__ = [
    "Advisory",
    "AdvisorySource",
    "AffectedImport",
    "FileAdvisorySource",
    "PackageAdvisories",
    "PackageMention",
    "SourceDocument",
    "StoredAdvisories",
    "advisory_sources",
    "cyclonedx_transitive",
    "from_purl",
    "join_usage",
    "load_advisories",
    "merge_mentions",
    "parse_document",
]
