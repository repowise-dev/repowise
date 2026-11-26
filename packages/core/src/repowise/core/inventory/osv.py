"""Reading OSV vulnerability records: severity, aliases, affected symbols, fixed versions.

Pure functions over decoded OSV JSON (https://ossf.github.io/osv-schema/). No
transport: a feed that fetches records lives in a plugin behind the
``advisory-source`` seam, and calls these to read what it fetched.

* Severity: CVSS v4, then v3, then v2 from ``severity[]``; then the
  ``database_specific.severity`` label GHSA attaches; else ``unknown``.
* Aliases: records describing the same CVE collapse to one (GHSA preferred).
* Affected symbols: ``ecosystem_specific.imports`` (authoritative for Go, where
  the Go vulndb names package paths and symbols) and the loose
  ``database_specific.symbols`` / ``functions`` hints other databases attach.
  None present means no symbol-level claim can be made.
* Fixed versions: the ``fixed`` events of SEMVER and ECOSYSTEM ranges, ordered
  for the package's own ecosystem (:mod:`repowise.core.inventory.versions`).
"""

from __future__ import annotations

import math
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, TypeVar

from repowise.core.inventory.versions import Range, version_key

_T = TypeVar("_T")


@dataclass(frozen=True, slots=True)
class AffectedImport:
    """An affected package path and the symbols in it (OSV ``imports[]``).

    An empty *path* means the record names symbols without saying which package
    path holds them; empty *symbols* means the whole path is affected.
    """

    path: str
    symbols: tuple[str, ...] = ()


# -- severity ---------------------------------------------------------------


def label_for_score(score: float) -> str:
    """The CVSS qualitative rating of a 0-10 base score."""
    if score >= 9.0:
        return "critical"
    if score >= 7.0:
        return "high"
    if score >= 4.0:
        return "medium"
    if score > 0.0:
        return "low"
    return "unknown"


def _metrics(vector: str) -> dict[str, str]:
    return dict(p.split(":", 1) for p in vector.split("/") if ":" in p)


_V3_AV = {"N": 0.85, "A": 0.62, "L": 0.55, "P": 0.2}
_V3_AC = {"L": 0.77, "H": 0.44}
_V3_PR_U = {"N": 0.85, "L": 0.62, "H": 0.27}
_V3_PR_C = {"N": 0.85, "L": 0.68, "H": 0.5}
_V3_UI = {"N": 0.85, "R": 0.62}
_V3_CIA = {"H": 0.56, "L": 0.22, "N": 0.0}


def _v3_roundup(value: float, v30: bool) -> float:
    if v30:
        return math.ceil(value * 10 - 1e-9) / 10
    # CVSS 3.1 Roundup, written to avoid floating point drift.
    scaled = round(value * 100_000)
    return scaled / 100_000 if scaled % 10_000 == 0 else (math.floor(scaled / 10_000) + 1) / 10


def _cvss3(vector: str) -> float:
    m = _metrics(vector)
    changed = m["S"] == "C"
    iss = 1 - (1 - _V3_CIA[m["C"]]) * (1 - _V3_CIA[m["I"]]) * (1 - _V3_CIA[m["A"]])
    impact = 7.52 * (iss - 0.029) - 3.25 * (iss - 0.02) ** 15 if changed else 6.42 * iss
    pr = (_V3_PR_C if changed else _V3_PR_U)[m["PR"]]
    exploit = 8.22 * _V3_AV[m["AV"]] * _V3_AC[m["AC"]] * pr * _V3_UI[m["UI"]]
    if impact <= 0:
        return 0.0
    total = min(1.08 * (impact + exploit) if changed else impact + exploit, 10)
    return _v3_roundup(total, vector.startswith("CVSS:3.0/"))


_V2_AV = {"L": 0.395, "A": 0.646, "N": 1.0}
_V2_AC = {"H": 0.35, "M": 0.61, "L": 0.71}
_V2_AU = {"M": 0.45, "S": 0.56, "N": 0.704}
_V2_CIA = {"N": 0.0, "P": 0.275, "C": 0.660}


def _cvss2(vector: str) -> float:
    m = _metrics(vector)
    impact = 10.41 * (1 - (1 - _V2_CIA[m["C"]]) * (1 - _V2_CIA[m["I"]]) * (1 - _V2_CIA[m["A"]]))
    exploit = 20 * _V2_AV[m["AV"]] * _V2_AC[m["AC"]] * _V2_AU[m["Au"]]
    base = (0.6 * impact + 0.4 * exploit - 1.5) * (0 if impact == 0 else 1.176)
    return float(Decimal(str(base)).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP))


def cvss_base_score(sev_type: str, vector: str) -> float | None:
    """The base score of a CVSS vector, or ``None`` when it does not parse.

    v3.x and v2 are computed here from the specification's formulas. v4 needs
    the specification's macro-vector table, so it is scored only when the
    optional ``cvss`` package is installed; otherwise ``None``, and the caller
    falls through to the record's v3 vector or its severity label.
    """
    try:
        if sev_type == "CVSS_V3":
            return _cvss3(vector)
        if sev_type == "CVSS_V2":
            return _cvss2(vector)
        if sev_type == "CVSS_V4":
            from cvss import CVSS4  # optional; not a dependency

            return float(CVSS4(vector).base_score)
    except Exception:
        return None
    return None


def severity_from_osv(vuln: dict) -> tuple[float | None, str]:
    """``(score, label)`` from an OSV record's ``severity`` array.

    Precedence CVSS_V4, CVSS_V3, CVSS_V2; then the ``database_specific.severity``
    label (GHSA's MODERATE becomes ``medium``); then ``(None, "unknown")``.
    """
    vectors = _vectors(vuln)
    for sev_type in ("CVSS_V4", "CVSS_V3", "CVSS_V2"):
        vector = vectors.get(sev_type)
        if vector and (score := cvss_base_score(sev_type, vector)) is not None:
            label = label_for_score(score)
            # CVSS v2 has no critical band: 7.0 to 10.0 is high.
            return score, "high" if sev_type == "CVSS_V2" and label == "critical" else label
    return None, _db_label(vuln)


def _vectors(vuln: dict) -> dict[str, str]:
    """The first vector per CVSS type in ``severity[]``."""
    vectors: dict[str, str] = {}
    for entry in vuln.get("severity") or []:
        t, s = (entry.get("type"), entry.get("score")) if isinstance(entry, dict) else (None, None)
        if isinstance(t, str) and isinstance(s, str):
            vectors.setdefault(t, s)
    return vectors


def _db_label(vuln: dict) -> str:
    db = vuln.get("database_specific")
    label = str((db.get("severity") if isinstance(db, dict) else None) or "").lower()
    if label in ("critical", "high", "low"):
        return label
    return "medium" if label in ("moderate", "medium") else "unknown"


# -- aliases ----------------------------------------------------------------


def cve_aliases(vuln: dict) -> list[str]:
    """CVE ids for a record: its ``aliases``, plus its own id when that is a CVE."""
    out: list[str] = []
    osv_id = vuln.get("id") or ""
    if isinstance(osv_id, str) and osv_id.startswith("CVE-"):
        out.append(osv_id)
    for alias in vuln.get("aliases") or []:
        if isinstance(alias, str) and alias.startswith("CVE-") and alias not in out:
            out.append(alias)
    return out


def group_by_cve(items: Iterable[_T], cves: Callable[[_T], Sequence[str]]) -> list[list[_T]]:
    """Items linked by shared CVE ids, directly or through another item.

    Items with no CVE stand alone. Groups follow their first item's order.
    """
    items = list(items)
    parent = list(range(len(items)))

    def root(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    first: dict[str, int] = {}
    for i, item in enumerate(items):
        for c in cves(item):
            j = first.setdefault(c, i)
            a, b = root(i), root(j)
            parent[max(a, b)] = min(a, b)
    groups: dict[int, list[_T]] = {}
    for i, item in enumerate(items):
        groups.setdefault(root(i), []).append(item)
    return list(groups.values())


def dedupe_by_cve(
    items: Iterable[_T], cves: Callable[[_T], Sequence[str]], quality: Callable[[_T], Any]
) -> list[_T]:
    """One item per :func:`group_by_cve` group: the first with the highest *quality*."""
    return [max(group, key=quality) for group in group_by_cve(items, cves)]


def dedupe_by_alias(vulns: list[dict]) -> list[dict]:
    """Collapse OSV records describing the same CVE for one package.

    OSV aggregates databases, so one flaw often arrives as both a GHSA and a
    PYSEC record. The kept record is the one with a parseable severity, then a
    GHSA id (the richest metadata).
    """
    return dedupe_by_cve(
        vulns,
        cve_aliases,
        lambda v: (
            1 if v.get("severity") else 0,
            1 if str(v.get("id") or "").startswith("GHSA-") else 0,
        ),
    )


# -- one affected[] entry ---------------------------------------------------


def affected_imports(entry: dict) -> tuple[AffectedImport, ...]:
    """The affected package paths and symbols one ``affected[]`` entry names."""
    out: list[AffectedImport] = []
    for imp in _section(entry, "ecosystem_specific").get("imports") or []:
        if not isinstance(imp, dict):
            continue
        path = imp.get("path") if isinstance(imp.get("path"), str) else ""
        symbols = _strings(imp.get("symbols") or [])
        if path or symbols:
            out.append(AffectedImport(path, symbols))
    db = _section(entry, "database_specific")
    loose = _strings([*(db.get("symbols") or []), *(db.get("functions") or [])])
    if loose:
        out.append(AffectedImport("", loose))
    return tuple(out)


def _section(entry: dict, name: str) -> dict:
    value = entry.get(name)
    return value if isinstance(value, dict) else {}


def _strings(values: Iterable[Any]) -> tuple[str, ...]:
    """The non-empty strings in *values*, first spelling kept."""
    return tuple(dict.fromkeys(v for v in values if isinstance(v, str) and v))


def affected_ranges(entry: dict) -> tuple[tuple[tuple[str, str], ...], ...]:
    """The SEMVER and ECOSYSTEM ranges of one entry as ``((kind, version), ...)``.

    GIT ranges are left out: they bound commits, not released versions. An
    entry with ranges but none of those, and no ``versions`` list, gives one
    empty range, which evaluates as undecided rather than vanishing.
    """
    out = []
    for rng in entry.get("ranges") or []:
        if not isinstance(rng, dict) or rng.get("type") not in ("SEMVER", "ECOSYSTEM"):
            continue
        events = tuple(
            (kind, value)
            for event in rng.get("events") or []
            if isinstance(event, dict)
            for kind, value in event.items()
            if isinstance(value, str)
        )
        if events:
            out.append(events)
    if not out and entry.get("ranges") and not affected_versions(entry):
        out.append(())
    return tuple(out)


def affected_versions(entry: dict) -> tuple[str, ...]:
    """The explicit ``versions`` list of one entry."""
    return tuple(v for v in entry.get("versions") or [] if isinstance(v, str))


def fixed_versions(ranges: Iterable[Range]) -> tuple[str, ...]:
    """Every ``fixed`` event across *ranges*, in the order listed."""
    return tuple(dict.fromkeys(v for events in ranges for kind, v in events if kind == "fixed"))


def pick_fixed(ecosystem: str | None, fixed: Sequence[str], installed: str | None) -> str | None:
    """The version to upgrade to: the smallest of *fixed* above *installed*.

    Ordered for *ecosystem*. ``None`` when no fix is above the installed
    version, or when there is no fix. With no order known, the only fix when
    there is one, else ``None``: which branch applies cannot be told.
    """
    if not fixed:
        return None
    current = version_key(ecosystem, installed)
    comparable = [(k, raw) for raw in fixed if (k := version_key(ecosystem, raw)) is not None]
    if current is not None and comparable:
        above = [(k, raw) for k, raw in comparable if k > current]
        # No fix above the version in use: none of these fixes it.
        return min(above)[1] if above else None
    return fixed[0] if len(fixed) == 1 else None


# -- purl-keyed helpers over a whole record ---------------------------------


def _entries_for_purl(vuln: dict, purl: str) -> list[dict]:
    bare = purl.split("@", 1)[0].lower()
    out = []
    for entry in vuln.get("affected") or []:
        if not isinstance(entry, dict):
            continue
        pkg = entry.get("package") if isinstance(entry.get("package"), dict) else {}
        a_purl = str(pkg.get("purl") or "").split("@", 1)[0].lower()
        if a_purl and a_purl != bare:
            continue
        out.append(entry)
    return out


def extract_affected_imports(vuln: dict, purl: str) -> dict | None:
    """``{"paths": [...], "symbols": [...]}`` for the package *purl* names, or ``None``.

    ``None`` when the record carries no symbol-level data: the caller must then
    make no reachability claim. Entries without a purl count as this package's.
    """
    paths: list[str] = []
    symbols: list[str] = []
    for entry in _entries_for_purl(vuln, purl):
        for imp in affected_imports(entry):
            if imp.path and imp.path not in paths:
                paths.append(imp.path)
            symbols.extend(s for s in imp.symbols if s not in symbols)
    if not paths and not symbols:
        return None
    return {"paths": paths, "symbols": symbols}


def extract_fixed_version(vuln: dict, purl: str, installed_version: str) -> str | None:
    """The version to upgrade to for the package *purl* names, ordered for its ecosystem."""
    from repowise.core.inventory.advisories import from_purl

    ecosystem = from_purl(purl)[0]
    ranges = [r for entry in _entries_for_purl(vuln, purl) for r in affected_ranges(entry)]
    return pick_fixed(ecosystem, fixed_versions(ranges), installed_version)


__all__ = [
    "AffectedImport",
    "affected_imports",
    "affected_ranges",
    "affected_versions",
    "cve_aliases",
    "cvss_base_score",
    "dedupe_by_alias",
    "dedupe_by_cve",
    "extract_affected_imports",
    "extract_fixed_version",
    "fixed_versions",
    "group_by_cve",
    "label_for_score",
    "pick_fixed",
    "severity_from_osv",
]
