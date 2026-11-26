"""Version ordering per ecosystem, and OSV affected-range evaluation.

Three orderings are known:

* ``pypi``: PEP 440, through ``packaging`` (``1.0rc1 < 1.0 < 1.0.post1``);
* ``npm`` and ``cargo``: SemVer 2.0 (``1.0.0-rc.1 < 1.0.0``; build metadata ignored);
* ``go``: module versions, SemVer with a ``v`` prefix. A pseudo-version
  (``v0.0.0-20230101000000-abcdef123456``) is a SemVer prerelease and orders as one;
  ``+incompatible`` is build metadata.

Every other ecosystem, or a version that does not parse, gives ``None``: the
caller then makes no claim either way. A wrong "not affected" hides a real
advisory, so every unknown stays unknown.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from typing import Any

_SEMVER = re.compile(
    r"^v?(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
    r"(?:-([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?"
    r"(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?$"
)

#: Ecosystems whose versions order as SemVer (an optional "v" prefix is accepted).
_SEMVER_ECOSYSTEMS = frozenset({"npm", "cargo", "go"})


def _semver_key(version: str) -> tuple | None:
    m = _SEMVER.match(version.strip())
    if m is None:
        return None
    major, minor, patch, pre = m.groups()
    if pre is None:
        # A release orders after every prerelease of the same core version.
        pre_key: tuple = (1,)
    else:
        idents = tuple((0, int(p), "") if p.isdigit() else (1, 0, p) for p in pre.split("."))
        pre_key = (0, idents)
    return (int(major), int(minor), int(patch), pre_key)


def _pep440_key(version: str) -> Any:
    from packaging.version import InvalidVersion, Version

    try:
        return Version(version.strip())
    except InvalidVersion:
        return None


def version_key(ecosystem: str | None, version: str | None) -> Any:
    """A sortable key for *version* in *ecosystem*, or ``None`` when it has no known order."""
    if not version or not isinstance(version, str):
        return None
    if ecosystem == "pypi":
        return _pep440_key(version)
    if ecosystem in _SEMVER_ECOSYSTEMS:
        return _semver_key(version)
    return None


def compare_versions(ecosystem: str | None, a: str, b: str) -> int | None:
    """-1, 0 or 1 as *a* orders before, with or after *b*; ``None`` when either has no order."""
    ka, kb = version_key(ecosystem, a), version_key(ecosystem, b)
    if ka is None or kb is None:
        return None
    return (ka > kb) - (ka < kb)


# A bare version is exact in Poetry and Pipenv tables (PEP 508 always has an operator).
_PYPI_EXACT = re.compile(r"^\s*(?:===?)?\s*([^,;*\s<>=!~^]+)\s*$")
_NPM_EXACT = re.compile(r"^\s*=?\s*v?(\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.+-]+)?)\s*$")
_CARGO_EXACT = re.compile(r"^\s*=\s*(\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.+-]+)?)\s*$")


def exact_version(ecosystem: str | None, spec: str | None) -> str | None:
    """The one version a declared *spec* pins, or ``None`` for a range or an unknown form.

    ``==2.31.0`` or a bare ``2.31.0`` (pypi), ``1.2.3`` or ``=1.2.3`` (npm), ``=1.2.3`` (cargo, where a
    bare ``1.2.3`` means ``^1.2.3``). A go.mod ``require`` is a minimum that
    the build may raise, so it pins nothing here.
    """
    if not spec or not isinstance(spec, str):
        return None
    if ecosystem == "pypi":
        m = _PYPI_EXACT.match(spec)
    elif ecosystem == "npm":
        m = _NPM_EXACT.match(spec)
    elif ecosystem == "cargo":
        m = _CARGO_EXACT.match(spec)
    else:
        # go.mod names a minimum: the build may select a higher version.
        return None
    if m is None or version_key(ecosystem, m.group(1)) is None:
        return None
    return m.group(1)


#: One OSV range as ``((kind, version), ...)``, kind being introduced, fixed,
#: last_affected or limit.
Range = Sequence[tuple[str, str]]

_EVENT_ORDER = {"introduced": 0, "fixed": 1, "last_affected": 1, "limit": 1}


def _in_range(key: Any, events: Range, ecosystem: str) -> bool | None:
    keyed = []
    for kind, value in events:
        if kind not in _EVENT_ORDER:
            continue
        if kind == "introduced" and value == "0":
            keyed.append(((0,), 0, kind, None))
            continue
        k = version_key(ecosystem, value)
        if k is None:
            return None
        keyed.append(((1, k), _EVENT_ORDER[kind], kind, k))
    if not any(kind == "introduced" for _, _, kind, _ in keyed):
        # The schema requires an introduced event; without one, claim nothing.
        return None
    affected = False
    # The OSV schema's evaluation: events in version order, each one switching
    # the state on (introduced) or off (fixed, limit, past last_affected).
    for _, _, kind, k in sorted(keyed, key=lambda e: (e[0], e[1])):
        if kind == "introduced":
            if k is None or key >= k:
                affected = True
        elif kind in ("fixed", "limit"):
            if key >= k:
                affected = False
        elif kind == "last_affected" and key > k:
            affected = False
    return affected


def affected_by(
    ecosystem: str | None,
    version: str | None,
    ranges: Iterable[Range] = (),
    versions: Iterable[str] = (),
) -> bool | None:
    """Whether *version* falls in an OSV affected entry (its ``ranges`` and ``versions``).

    ``True`` affected, ``False`` proven outside every range, ``None`` when it
    cannot be decided: no version, an ecosystem with no known order, an
    unparseable bound, or an entry with neither ranges nor versions.
    """
    ranges, versions = list(ranges), list(versions)
    if not version:
        return None
    key = version_key(ecosystem, version)
    listed = [v for v in versions if isinstance(v, str)]
    if version in listed or (
        key is not None and any(version_key(ecosystem, v) == key for v in listed)
    ):
        return True
    if key is None or ecosystem is None:
        return None
    if not ranges:
        # An exhaustive versions list that misses this version proves it clear.
        return False if listed else None
    results = [_in_range(key, events, ecosystem) for events in ranges]
    if any(r is True for r in results):
        return True
    if any(r is None for r in results):
        return None
    return False


__all__ = ["Range", "affected_by", "compare_versions", "exact_version", "version_key"]
