"""Onboarding subkind: Getting Started.

The page where reading stops and doing starts — clone, install, build,
run, test. Purely mechanical, grounded in either a recognised manifest
(``package.json``, ``pyproject.toml``, ``go.mod`` …) or a README section
that already explains setup.

Gate: at least one parsed manifest **or** a README at the repo root with
a recognisable install/run/build/test heading. Skip for pure-docs or
config-only repos with no detectable build system.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import PurePosixPath

from ...entry_points import orientation_entry_points
from ..registry import SubkindSpec, register
from ..signals import OnboardingSignals
from ..slots import SLOT_GETTING_STARTED, SLOT_TITLES

# Heading patterns we treat as "setup-relevant" when scanning a README.
# Lowercased; matched against the trimmed line after the leading `#`s.
_README_HEADINGS: tuple[tuple[str, str], ...] = (
    ("install", "Install"),
    ("installation", "Install"),
    ("quickstart", "Quickstart"),
    ("quick start", "Quickstart"),
    ("getting started", "Getting Started"),
    ("setup", "Setup"),
    ("run", "Run"),
    ("running", "Run"),
    ("usage", "Usage"),
    ("build", "Build"),
    ("test", "Test"),
    ("testing", "Test"),
    ("development", "Development"),
    ("contributing", "Contributing"),
)

_README_FILENAMES = ("README.md", "readme.md", "README.MD", "README", "Readme.md")
_MAX_README_SECTION_CHARS = 800
_MAX_SETUP_DOCUMENTS = 4
_MAX_SETUP_SECTIONS = 10
_MAX_MANIFEST_SCRIPTS = 16
_MAX_DEPS_LISTED = 12

_SCRIPT_PRIORITY = {
    "install": 0,
    "setup": 1,
    "dev": 2,
    "start": 3,
    "build": 4,
    "test": 5,
    "lint": 6,
    "typecheck": 7,
    "type-check": 7,
}

_SETUP_DOCUMENT_STEMS = {
    "contributing": 0,
    "development": 1,
    "developing": 1,
    "install": 2,
    "installation": 2,
    "getting_started": 3,
    "getting-started": 3,
    "setup": 3,
}


@dataclass
class ReadmeSection:
    heading: str
    body: str
    source_path: str = "README.md"
    source_kind: str = "setup_document"


@dataclass(frozen=True)
class ManifestScript:
    name: str
    command: str
    manifest_path: str
    working_directory: str
    invocation: str
    runner: str
    source_kind: str = "manifest_script"


@dataclass(frozen=True)
class ManifestSource:
    manifest_path: str
    working_directory: str
    runner: str
    source_kind: str = "manifest_script"


@dataclass
class GettingStartedContext:
    repo_name: str
    package_managers: list[str] = field(default_factory=list)
    runtime_dependencies: list[dict] = field(default_factory=list)
    dev_dependencies: list[dict] = field(default_factory=list)
    readme_sections: list[ReadmeSection] = field(default_factory=list)
    manifest_scripts: list[ManifestScript] = field(default_factory=list)
    manifest_sources: list[ManifestSource] = field(default_factory=list)
    entry_points: list[str] = field(default_factory=list)


def _setup_document_priority(path: str) -> tuple[int, int, str] | None:
    """Return a deterministic authority rank for a setup-bearing document."""
    normalized = path.replace("\\", "/").strip("/")
    pure = PurePosixPath(normalized)
    name = pure.name.lower()
    stem = name.rsplit(".", 1)[0]
    depth = len(pure.parts) - 1
    if depth == 0 and name in {candidate.lower() for candidate in _README_FILENAMES}:
        return (10, depth, normalized.lower())
    if stem not in _SETUP_DOCUMENT_STEMS or pure.suffix.lower() not in {".md", ".rst", ".txt"}:
        return None
    # Root and conventional contributor/documentation locations outrank deep,
    # package-local files while remaining repository-generic.
    conventional = 0 if depth == 0 or pure.parts[0].lower() in {".github", "docs"} else 1
    return (_SETUP_DOCUMENT_STEMS[stem] + conventional, depth, normalized.lower())


def _find_setup_documents(source_map: dict[str, bytes]) -> list[tuple[str, bytes]]:
    ranked: list[tuple[tuple[int, int, str], str, bytes]] = []
    for path, data in source_map.items():
        if not data:
            continue
        priority = _setup_document_priority(path)
        if priority is not None:
            ranked.append((priority, path.replace("\\", "/"), data))
    ranked.sort(key=lambda item: item[0])
    root_readme = next(
        (item for item in ranked if item[0][0] == 10 and item[0][1] == 0),
        None,
    )
    selected = ranked[:_MAX_SETUP_DOCUMENTS]
    if root_readme is not None and root_readme not in selected:
        selected = [*ranked[: _MAX_SETUP_DOCUMENTS - 1], root_readme]
        selected.sort(key=lambda item: item[0])
    return [(path, data) for _, path, data in selected]


def _extract_setup_sections(
    readme: bytes, *, source_path: str = "README.md"
) -> list[ReadmeSection]:
    """Pull setup-relevant sections out of a README body.

    Stops each section at the next heading of any level, then truncates to
    keep the prompt budget small.
    """
    try:
        text = readme.decode("utf-8", errors="replace")
    except Exception:
        return []

    lines = text.splitlines()
    sections: list[ReadmeSection] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        heading_match = re.match(r"^(#{1,6})\s+(.+?)\s*$", line)
        if not heading_match:
            i += 1
            continue
        heading_text = heading_match.group(2).strip().lower()
        # Match against any of our known setup headings.
        canonical: str | None = None
        for pattern, label in _README_HEADINGS:
            if pattern == heading_text or heading_text.startswith(pattern + " "):
                canonical = label
                break
        if canonical is None:
            i += 1
            continue
        # Collect body until the next heading of any depth.
        body_lines: list[str] = []
        j = i + 1
        while j < len(lines) and not re.match(r"^#{1,6}\s+\S", lines[j]):
            body_lines.append(lines[j])
            j += 1
        body = "\n".join(body_lines).strip()
        if body:
            sections.append(
                ReadmeSection(
                    heading=canonical,
                    body=body[:_MAX_README_SECTION_CHARS],
                    source_path=source_path,
                )
            )
        i = j
    return sections


def _package_runner(path: str, source_map: dict[str, bytes]) -> str:
    directory = str(PurePosixPath(path).parent)
    prefixes = ("" if directory == "." else directory + "/", "")
    normalized_paths = {candidate.replace("\\", "/") for candidate in source_map}
    for prefix in prefixes:
        if prefix + "pnpm-lock.yaml" in normalized_paths:
            return "pnpm"
        if prefix + "yarn.lock" in normalized_paths:
            return "yarn"
        if prefix + "bun.lock" in normalized_paths or prefix + "bun.lockb" in normalized_paths:
            return "bun"
        if (
            prefix + "package-lock.json" in normalized_paths
            or prefix + "npm-shrinkwrap.json" in normalized_paths
        ):
            return "npm"
    # package.json defines npm-compatible scripts. npm is the conservative
    # runner only when no repository lockfile establishes another one.
    return "npm"


def _manifest_scripts(source_map: dict[str, bytes]) -> list[ManifestScript]:
    """Extract bounded, exact package.json scripts with their working directory."""
    manifests = sorted(
        (
            (path.replace("\\", "/"), data)
            for path, data in source_map.items()
            if PurePosixPath(path.replace("\\", "/")).name.lower() == "package.json"
        ),
        key=lambda item: (len(PurePosixPath(item[0]).parts), item[0]),
    )
    scripts: list[ManifestScript] = []
    for path, data in manifests:
        try:
            payload = json.loads(data.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            continue
        declared = payload.get("scripts") if isinstance(payload, dict) else None
        if not isinstance(declared, dict):
            continue
        working_directory = str(PurePosixPath(path).parent)
        runner = _package_runner(path, source_map)
        ordered_scripts = sorted(
            declared.items(),
            key=lambda item: (_SCRIPT_PRIORITY.get(str(item[0]).lower(), 100), str(item[0])),
        )
        for name, command in ordered_scripts:
            if not isinstance(name, str) or not isinstance(command, str):
                continue
            scripts.append(
                ManifestScript(
                    name=name,
                    command=command,
                    manifest_path=path,
                    working_directory=working_directory,
                    invocation=f"{runner} run {name}",
                    runner=runner,
                )
            )
            if len(scripts) >= _MAX_MANIFEST_SCRIPTS:
                return scripts
    return scripts


def _partition_dependencies(
    external_systems: tuple[dict, ...],
) -> tuple[list[str], list[dict], list[dict]]:
    """Split external_systems into (package_managers, runtime_deps, dev_deps).

    The manifest parsers already record ``ecosystem`` (npm/pypi/cargo/…),
    ``version`` and ``is_dev_dep``. Anything we can't classify falls into
    runtime.

    Key names must match what the orchestrator actually emits (see
    ``pipeline/orchestrator.py``, which builds these dicts straight off
    ``ExternalSystemRecord``). Reading ``is_dev`` instead of ``is_dev_dep``
    silently sorted every dev dependency into runtime, and omitting
    ``version`` made the template's ``{% if d.version %}`` raise under
    StrictUndefined, which lost the whole getting-started page.
    """
    package_managers: list[str] = []
    runtime: list[dict] = []
    dev: list[dict] = []
    seen_ecosystems: set[str] = set()

    for sys in external_systems:
        eco = str(sys.get("ecosystem", "") or "").strip()
        if eco and eco not in seen_ecosystems:
            package_managers.append(eco)
            seen_ecosystems.add(eco)
        entry = {
            "name": sys.get("name", ""),
            "ecosystem": eco,
            "category": sys.get("category", "library"),
            # Always present, so the template can test it without tripping
            # StrictUndefined. Normalized to "" because the record's version is
            # Optional and a None would render as "None".
            "version": str(sys.get("version") or "").strip(),
        }
        if sys.get("is_dev_dep"):
            dev.append(entry)
        else:
            runtime.append(entry)

    return package_managers, runtime[:_MAX_DEPS_LISTED], dev[:_MAX_DEPS_LISTED]


def _build(signals: OnboardingSignals) -> GettingStartedContext | None:
    package_managers, runtime, dev = _partition_dependencies(signals.external_systems)
    contributor_sections: list[ReadmeSection] = []
    root_readme_sections: list[ReadmeSection] = []
    for source_path, document in _find_setup_documents(signals.source_map):
        sections = _extract_setup_sections(document, source_path=source_path)
        if "/" not in source_path and source_path.lower().startswith("readme"):
            root_readme_sections.extend(sections)
        else:
            contributor_sections.extend(sections)
    if root_readme_sections:
        readme_sections = [
            *contributor_sections[: _MAX_SETUP_SECTIONS - 1],
            root_readme_sections[0],
        ]
    else:
        readme_sections = contributor_sections[:_MAX_SETUP_SECTIONS]
    manifest_scripts = _manifest_scripts(signals.source_map)
    manifest_sources: list[ManifestSource] = []
    seen_manifests: set[str] = set()
    for script in manifest_scripts:
        if script.manifest_path in seen_manifests:
            continue
        seen_manifests.add(script.manifest_path)
        manifest_sources.append(
            ManifestSource(
                manifest_path=script.manifest_path,
                working_directory=script.working_directory,
                runner=script.runner,
            )
        )

    # Gate: need at least one signal source. Without a manifest *and*
    # without a README setup section, the page would be all speculation.
    if not package_managers and not readme_sections and not manifest_scripts:
        return None

    return GettingStartedContext(
        repo_name=signals.repo_name,
        package_managers=package_managers,
        runtime_dependencies=runtime,
        dev_dependencies=dev,
        readme_sections=readme_sections,
        manifest_scripts=manifest_scripts,
        manifest_sources=manifest_sources,
        entry_points=orientation_entry_points(signals.repo_structure, limit=6),
    )


def _evidence_references(ctx: object) -> tuple[str, ...]:
    """Prioritize setup documents and manifests in the shared evidence channel."""
    if not isinstance(ctx, GettingStartedContext):
        return ()
    references: list[str] = []
    for section in ctx.readme_sections:
        if section.source_path not in references:
            references.append(section.source_path)
    for script in ctx.manifest_scripts:
        if script.manifest_path not in references:
            references.append(script.manifest_path)
    return tuple(references)


register(
    SubkindSpec(
        slot=SLOT_GETTING_STARTED,
        title=SLOT_TITLES[SLOT_GETTING_STARTED],
        template="getting_started.j2",
        build_context=_build,
        evidence_references=_evidence_references,
    )
)
