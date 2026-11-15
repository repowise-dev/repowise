"""Onboarding subkind: Getting Started.

The page where reading stops and doing starts. Part one is the user path:
install the project, use it for the first time, see that it worked. Part two
is working on the repository itself.

The two parts read different documents. The user path comes from the README
and the user guides (quickstart, install, usage, ``docs/start``), in Markdown or
reStructuredText; the contributor path comes from CONTRIBUTING and development
guides. Ranking contributing documents first made the page a guide to
building the repository's own frontend, and never said how to run the product.

Gate: some setup source exists, a user or contributor document, a declared
manifest script, or a recognised package ecosystem.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import PurePosixPath

from ...entry_points import orientation_entry_points
from ..docs import DocSection, doc_sections, is_dev_doc, is_user_doc, rank_sections
from ..registry import SubkindSpec, register
from ..signals import OnboardingSignals
from ..slots import SLOT_GETTING_STARTED, SLOT_HOW_IT_WORKS, SLOT_TITLES
from ..surface import Surface, Surfaces

_MAX_MANIFEST_SCRIPTS = 16
_MAX_DEPS_LISTED = 12
#: Prose range under the default style: user path, then the develop block.
_WORDS = (800, 1000)

_USER_WORDS = (
    "install", "quickstart", "quick start", "getting started", "set it up", "set up",
    "setup", "usage", "first run", "requirements",
)
_DEV_WORDS = ("develop", "contribut", "local", "from source", "test", "build", "lint")

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


@dataclass(frozen=True)
class ManifestScript:
    name: str
    command: str
    manifest_path: str
    working_directory: str
    invocation: str
    runner: str
    source_kind: str = "manifest_script"


@dataclass
class GettingStartedContext:
    repo_name: str
    package_managers: list[str] = field(default_factory=list)
    runtime_dependencies: list[dict] = field(default_factory=list)
    dev_dependencies: list[dict] = field(default_factory=list)
    #: The user path: README and user-guide sections, best first.
    user_docs: list[DocSection] = field(default_factory=list)
    #: The contributor path: CONTRIBUTING and development guides.
    dev_docs: list[DocSection] = field(default_factory=list)
    programs: list[str] = field(default_factory=list)
    commands: list[Surface] = field(default_factory=list)
    tools: list[Surface] = field(default_factory=list)
    api: list[Surface] = field(default_factory=list)
    manifest_scripts: list[ManifestScript] = field(default_factory=list)
    entry_points: list[str] = field(default_factory=list)
    #: Wiki pages the reader can open next, by title.
    neighbours: list[str] = field(default_factory=list)
    word_budget: tuple[int, int] = _WORDS


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

    Key names must match what the orchestrator emits (``is_dev_dep``,
    ``version``); a missing ``version`` key would raise under StrictUndefined
    in the template and lose the page.
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
            "version": str(sys.get("version") or "").strip(),
        }
        (dev if sys.get("is_dev_dep") else runtime).append(entry)

    return package_managers, runtime[:_MAX_DEPS_LISTED], dev[:_MAX_DEPS_LISTED]


def _user_doc_filter(path: str) -> bool:
    return is_user_doc(path) and not is_dev_doc(path)


def _build(signals: OnboardingSignals) -> GettingStartedContext | None:
    package_managers, runtime, dev = _partition_dependencies(signals.external_systems)
    sections = doc_sections(signals.doc_sources or signals.source_map)
    found = signals.surfaces or Surfaces()
    programs = found.programs
    typed = [f"{p} " for p in programs]
    user_docs = rank_sections(
        sections, typed + list(_USER_WORDS), heading_words=_USER_WORDS,
        path_filter=_user_doc_filter, char_cap=16000, per_section=3000,
    )
    dev_docs = rank_sections(
        sections, typed + list(_DEV_WORDS + _USER_WORDS), heading_words=_DEV_WORDS,
        path_filter=is_dev_doc, char_cap=7000, per_section=2400,
    )
    manifest_scripts = _manifest_scripts(signals.source_map)
    if not (package_managers or user_docs or dev_docs or manifest_scripts):
        return None
    neighbours = [SLOT_TITLES[SLOT_HOW_IT_WORKS], *(j.title for j in signals.journeys)]
    return GettingStartedContext(
        repo_name=signals.repo_name,
        package_managers=package_managers,
        runtime_dependencies=runtime,
        dev_dependencies=dev,
        user_docs=user_docs,
        dev_docs=dev_docs,
        programs=programs,
        commands=found.commands[:8],
        tools=found.tools[:8],
        api=found.api,
        manifest_scripts=manifest_scripts,
        entry_points=orientation_entry_points(signals.repo_structure, limit=6),
        neighbours=neighbours,
    )


def _evidence_references(ctx: object) -> tuple[str, ...]:
    """The manifests whose scripts the page may run.

    Script keys are untrusted repository text, so they reach the model only
    inside the bounded evidence channel, never in the prompt's own words. The
    documentation sections are already in the prompt, each with its span.
    """
    if not isinstance(ctx, GettingStartedContext):
        return ()
    return tuple(dict.fromkeys(s.manifest_path for s in ctx.manifest_scripts))


register(
    SubkindSpec(
        slot=SLOT_GETTING_STARTED,
        title=SLOT_TITLES[SLOT_GETTING_STARTED],
        template="getting_started.j2",
        build_context=_build,
        evidence_references=_evidence_references,
    )
)
