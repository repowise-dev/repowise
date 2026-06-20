"""Parse Python manifests: ``pyproject.toml`` (PEP 621) and ``requirements*.txt``.

``pyproject.toml`` covers PEP 621 (``[project] dependencies``,
``[project.optional-dependencies]``) and the older Poetry layout
(``[tool.poetry.dependencies]``, ``[tool.poetry.group.*.dependencies]``).
``requirements*.txt`` is parsed line-by-line.

Sibling repowise/internal packages (anything declared as a workspace member
or path dependency) are skipped — they are containers, not external systems.
"""

from __future__ import annotations

import re
from pathlib import Path

try:
    import tomllib  # type: ignore[import-not-found]
except ModuleNotFoundError:  # pragma: no cover — Python <3.11
    import tomli as tomllib  # type: ignore[no-redef]

from .base import ExternalSystemRecord
from .classifier import classify, display_name_for
from .io_kind import classify_io_kind

filenames: tuple[str, ...] = ("pyproject.toml",)
ecosystem: str = "pypi"

_REQ_LINE = re.compile(
    r"""^
    \s*
    (?P<name>[A-Za-z0-9_][A-Za-z0-9_.\-]*)
    (?:\[[^\]]*\])?
    \s*
    (?P<spec>[^;#\s]*)
    """,
    re.VERBOSE,
)


def parse(manifest_path: Path, repo_root: Path) -> list[ExternalSystemRecord]:
    name = manifest_path.name
    if name == "pyproject.toml":
        return _parse_pyproject(manifest_path, repo_root)
    if name.startswith("requirements") and name.endswith(".txt"):
        return _parse_requirements(manifest_path, repo_root, is_dev="dev" in name.lower())
    return []


def _parse_pyproject(manifest_path: Path, repo_root: Path) -> list[ExternalSystemRecord]:
    try:
        data = tomllib.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        return []
    if not isinstance(data, dict):
        return []

    declared_in = manifest_path.relative_to(repo_root).as_posix()
    records: list[ExternalSystemRecord] = []
    seen: set[str] = set()

    project = data.get("project")
    if isinstance(project, dict):
        for entry in project.get("dependencies", []) or []:
            _add_pep508(records, seen, entry, declared_in, is_dev=False)
        opt = project.get("optional-dependencies")
        if isinstance(opt, dict):
            for group, entries in opt.items():
                is_dev = group in {"dev", "test", "tests", "lint", "docs", "typing"}
                for entry in entries or []:
                    _add_pep508(records, seen, entry, declared_in, is_dev=is_dev)

    poetry = data.get("tool", {}).get("poetry") if isinstance(data.get("tool"), dict) else None
    if isinstance(poetry, dict):
        for raw_name, spec in (poetry.get("dependencies") or {}).items():
            if raw_name == "python":
                continue
            if _is_path_dep(spec):
                continue
            _add_simple(records, seen, str(raw_name), _spec_version(spec), declared_in, is_dev=False)
        groups = poetry.get("group")
        if isinstance(groups, dict):
            for group_name, group_data in groups.items():
                is_dev = group_name in {"dev", "test", "tests", "lint", "docs"}
                deps = group_data.get("dependencies") if isinstance(group_data, dict) else None
                if not isinstance(deps, dict):
                    continue
                for raw_name, spec in deps.items():
                    if _is_path_dep(spec):
                        continue
                    _add_simple(records, seen, str(raw_name), _spec_version(spec), declared_in, is_dev=is_dev)

    return records


def _parse_requirements(
    manifest_path: Path, repo_root: Path, *, is_dev: bool
) -> list[ExternalSystemRecord]:
    try:
        text = manifest_path.read_text(encoding="utf-8")
    except OSError:
        return []
    declared_in = manifest_path.relative_to(repo_root).as_posix()
    records: list[ExternalSystemRecord] = []
    seen: set[str] = set()
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line or line.startswith("-"):
            continue
        _add_pep508(records, seen, line, declared_in, is_dev=is_dev)
    return records


def _add_pep508(
    records: list[ExternalSystemRecord],
    seen: set[str],
    entry: str,
    declared_in: str,
    *,
    is_dev: bool,
) -> None:
    if not isinstance(entry, str):
        return
    match = _REQ_LINE.match(entry)
    if not match:
        return
    name = match.group("name").strip()
    if not name:
        return
    version = match.group("spec").strip() or None
    _add_simple(records, seen, name, version, declared_in, is_dev=is_dev)


def _add_simple(
    records: list[ExternalSystemRecord],
    seen: set[str],
    name: str,
    version: str | None,
    declared_in: str,
    *,
    is_dev: bool,
) -> None:
    key = name.lower()
    if key in seen:
        return
    seen.add(key)
    records.append(
        ExternalSystemRecord(
            name=name,
            ecosystem=ecosystem,
            declared_in=declared_in,
            version=version,
            display_name=display_name_for(name),
            category=classify(name),
            io_kind=classify_io_kind(name),
            is_dev_dep=is_dev,
        )
    )


def _spec_version(spec: object) -> str | None:
    if isinstance(spec, str):
        s = spec.strip()
        return s or None
    if isinstance(spec, dict):
        v = spec.get("version")
        if isinstance(v, str):
            return v.strip() or None
    return None


def _is_path_dep(spec: object) -> bool:
    return isinstance(spec, dict) and ("path" in spec or "git" in spec)
