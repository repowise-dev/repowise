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

import re
from dataclasses import dataclass, field

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
_MAX_DEPS_LISTED = 12


@dataclass
class ReadmeSection:
    heading: str
    body: str


@dataclass
class GettingStartedContext:
    repo_name: str
    package_managers: list[str] = field(default_factory=list)
    runtime_dependencies: list[dict] = field(default_factory=list)
    dev_dependencies: list[dict] = field(default_factory=list)
    readme_sections: list[ReadmeSection] = field(default_factory=list)
    entry_points: list[str] = field(default_factory=list)


def _find_readme(source_map: dict[str, bytes]) -> bytes | None:
    """Return the first repo-root README we can find, or None."""
    for name in _README_FILENAMES:
        data = source_map.get(name)
        if data:
            return data
    # Fallback: any path whose basename matches and lives at repo root.
    for path, data in source_map.items():
        if "/" not in path and path.lower() in ("readme", "readme.md", "readme.txt"):
            return data
    return None


def _extract_setup_sections(readme: bytes) -> list[ReadmeSection]:
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
                ReadmeSection(heading=canonical, body=body[:_MAX_README_SECTION_CHARS])
            )
        i = j
    return sections


def _partition_dependencies(
    external_systems: tuple[dict, ...],
) -> tuple[list[str], list[dict], list[dict]]:
    """Split external_systems into (package_managers, runtime_deps, dev_deps).

    The manifest parsers already record ``ecosystem`` (npm/pypi/cargo/…)
    and ``is_dev``. Anything we can't classify falls into runtime.
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
        }
        if sys.get("is_dev"):
            dev.append(entry)
        else:
            runtime.append(entry)

    return package_managers, runtime[:_MAX_DEPS_LISTED], dev[:_MAX_DEPS_LISTED]


def _build(signals: OnboardingSignals) -> GettingStartedContext | None:
    package_managers, runtime, dev = _partition_dependencies(signals.external_systems)
    readme_sections: list[ReadmeSection] = []
    readme_bytes = _find_readme(signals.source_map)
    if readme_bytes is not None:
        readme_sections = _extract_setup_sections(readme_bytes)

    # Gate: need at least one signal source. Without a manifest *and*
    # without a README setup section, the page would be all speculation.
    if not package_managers and not readme_sections:
        return None

    return GettingStartedContext(
        repo_name=signals.repo_name,
        package_managers=package_managers,
        runtime_dependencies=runtime,
        dev_dependencies=dev,
        readme_sections=readme_sections,
        entry_points=list(getattr(signals.repo_structure, "entry_points", []))[:6],
    )


register(
    SubkindSpec(
        slot=SLOT_GETTING_STARTED,
        title=SLOT_TITLES[SLOT_GETTING_STARTED],
        template="getting_started.j2",
        build_context=_build,
    )
)
