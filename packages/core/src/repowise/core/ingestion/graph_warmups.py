"""Per-language warmup hooks that run before the graph-import phase.

Some languages (notably C# / .NET) need an expensive one-time index
built before any per-file import can be resolved. When that build runs
lazily on first import resolution, the progress bar appears frozen for
many minutes mid-phase and the cost is silently absorbed into
``graph.imports`` timing — making it indistinguishable from real
import-resolution work.

This module gives each language a place to declare a *warmup* function
that runs in its own phase event (``graph.<lang>_index``), before the
``graph.imports`` loop starts. Warmups are gated on whether any
parsed file actually uses the language, so a Python-only repo never
pays a Java index cost.

Adding a new language's warmup is one entry in :data:`_WARMUPS`.
Implementations live in the language's resolver subpackage so this
module stays language-agnostic.
"""

from __future__ import annotations

import contextlib
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from .languages.specs.cpp import INCLUDE_FRAGMENT_EXTENSIONS

if TYPE_CHECKING:
    from .models import ParsedFile
    from .resolvers import ResolverContext


# A warmup receives the resolver context and returns nothing. It may
# cache its result on ``ctx`` (the resolvers already use a per-context
# attribute cache); the dispatcher does not inspect the return value.
Warmup = Callable[["ResolverContext"], None]

# Paths the export-macro rescan reads. Include fragments belong here: a
# template implementation in a .inl carries the same project export macro as
# the header that declares it, and missing it leaves the symbol marked
# non-exported, which is what the dead-code pass then acts on.
_CPP_MACRO_SCAN_EXTS: tuple[str, ...] = (
    ".h", ".hpp", ".hxx", ".hh", ".h++", ".inc",
    ".c", ".cc", ".cpp", ".cxx", ".c++",
    *sorted(INCLUDE_FRAGMENT_EXTENSIONS),
)


def _warmup_jvm(ctx: ResolverContext) -> None:
    from .resolvers.jvm_workspace import get_or_build_jvm_index

    index = get_or_build_jvm_index(ctx)
    graph = getattr(ctx, "graph", None)
    if graph is None:
        return

    # Collect every FQN that is reached by a *resource* mechanism the
    # graph cannot see: META-INF/services lines, JPMS ``provides ... with``
    # directives (both merged into ``index.services``), and Spring Boot
    # autoconfig imports (Boot-2 ``spring.factories``, Boot-3 ``.imports``).
    # Stamp the defining file node as ``is_entry_point`` so the
    # unreachable-file pass treats it as live without a per-language
    # check on every node.
    entry_fqns: set[str] = set()
    for impls in index.services.values():
        entry_fqns.update(impls)
    for fqns in index.autoconfig_imports.values():
        entry_fqns.update(fqns)

    for fqn in entry_fqns:
        for path in index.files_for_fqn(fqn):
            node = graph.nodes.get(path)
            if node is not None:
                node["is_entry_point"] = True

    # Stamp every JVM source file under a non-``main`` Gradle source-set
    # (``testFixtures``, ``integrationTest``, ``javaPoet``, ``jcstress``,
    # ``jmh``, ``benchmarks``, …) as ``is_never_flag``. Gradle declares
    # these as first-class source sets that the build runs through their
    # own tasks; from a "source-imported by main code" perspective they
    # always look orphan. The build-script-discovered list generalises
    # beyond the hardcoded never-flag globs and picks up arbitrary
    # repo-defined names (Caffeine's ``javaPoet`` and ``jcstress`` are
    # not Gradle-builtin conventions). The check uses the workspace
    # source-set ``src_dirs`` discovered by ``jvm_gradle.py``.
    try:
        from .resolvers.jvm_gradle import get_or_build_jvm_gradle_index

        gradle_index = get_or_build_jvm_gradle_index(ctx)
    except Exception:
        gradle_index = None

    if gradle_index is not None:
        non_main_prefixes: list[str] = []
        for project in gradle_index.projects.values():
            base = project.root_dir.rstrip("/")
            for ss in project.source_sets.values():
                if ss.is_main:
                    continue
                for src_dir in ss.src_dirs:
                    prefix = f"{base}/{src_dir}/" if base else f"{src_dir}/"
                    non_main_prefixes.append(prefix)
        if non_main_prefixes:
            for node_name in list(graph.nodes()):
                s = str(node_name)
                if not (s.endswith(".java") or s.endswith(".kt")):
                    continue
                if any(s.startswith(p) for p in non_main_prefixes):
                    nd = graph.nodes.get(node_name)
                    if nd is not None:
                        nd["is_never_flag"] = True


def _warmup_cpp(ctx: ResolverContext) -> None:
    """Build the C/C++ workspace index and propagate workspace-discovered
    export macros back into the graph.

    The parser runs before the warmup, so symbols on public headers that
    are tagged with a project-defined export macro (``LEVELDB_EXPORT``,
    ``SEASTAR_API``, …) land as ``is_exported_symbol=False``. We re-mark
    them here by reading each symbol's signature text and checking it
    against the workspace's discovered macro set. This keeps the parser
    stateless w.r.t. the workspace while still surfacing the right
    visibility on the graph nodes the dead-code analyzer reads.

    A second pass scans each translation unit for *registration-macro*
    markers — ``PYBIND11_MODULE``, ``REGISTER_OP``,
    ``RCLCPP_COMPONENTS_REGISTER_NODE``, ``BOOST_CLASS_EXPORT``,
    ``LLVMFuzzerTestOneInput``, ``Q_OBJECT``, ``__attribute__((constructor))``,
    ``[[gnu::retain]]`` / ``[[gnu::used]]`` and the like — and stamps
    ``is_entry_point=True`` on the file node. These macros wire the file
    into a runtime registry at static-init time, so a static call edge
    will never exist; without this rescue, every such TU reads as
    ``unreachable_file``.
    """
    from .resolvers.cpp_workspace import get_or_build_cpp_index

    index = get_or_build_cpp_index(ctx)
    graph = getattr(ctx, "graph", None)
    if graph is None:
        return

    macros = index.project_export_macros
    parsed_files = getattr(ctx, "parsed_files", None) or {}

    if macros:
        for path, parsed in parsed_files.items():
            if not path.endswith(_CPP_MACRO_SCAN_EXTS):
                continue
            for sym in parsed.symbols:
                sig = sym.signature or ""
                if not sig:
                    continue
                # Check macro presence as a token — cheap substring with a
                # word-boundary check to avoid false matches inside other
                # identifiers.
                for macro in macros:
                    idx = sig.find(macro)
                    if idx == -1:
                        continue
                    before_ok = idx == 0 or not (sig[idx - 1].isalnum() or sig[idx - 1] == "_")
                    end = idx + len(macro)
                    after_ok = end >= len(sig) or not (sig[end].isalnum() or sig[end] == "_")
                    if before_ok and after_ok:
                        node = graph.nodes.get(sym.id)
                        if node is not None:
                            node["is_exported_symbol"] = True
                            if node.get("visibility") == "private":
                                node["visibility"] = "public"
                        break

    _mark_cpp_entry_point_files(parsed_files, graph, getattr(ctx, "source_map", None))


# Tokens whose presence means the surrounding TU wires itself into a
# runtime registry at static-init time. Every match marks the file node
# as an entry point so the dead-code analyzer treats it as live.
_CPP_ENTRY_MARKERS = (
    "PYBIND11_MODULE",
    "PYBIND11_EMBEDDED_MODULE",
    "BOOST_PYTHON_MODULE",
    "NAPI_MODULE",
    "REGISTER_OP",
    "REGISTER_KERNEL_BUILDER",
    "BOOST_CLASS_EXPORT",
    "PLUGINLIB_EXPORT_CLASS",
    "RCLCPP_COMPONENTS_REGISTER_NODE",
    "LLVMFuzzerTestOneInput",
    "Q_OBJECT",
    "Q_GADGET",
    "Q_NAMESPACE",
    "QML_ELEMENT",
    "QML_NAMED_ELEMENT",
    "__attribute__((constructor))",
    "__attribute__((used))",
    "[[gnu::retain]]",
    "[[gnu::used]]",
    "JNI_OnLoad",
)


def _read_warmup_source(
    path: str,
    parsed: Any,
    source_map: dict[str, bytes] | None,
) -> str | None:
    """Text of *path* for a marker scan, from *source_map* if it has it.

    Neither ``ParsedFile`` nor ``FileInfo`` carries the source, so before
    ``source_map`` existed these scans always re-opened the file. Decoding
    matches what the disk fallback below does (utf-8 / replace) so a hit and
    a miss can never disagree about the same bytes. Returns None when the
    file is unavailable, which the callers treat as "no markers".
    """
    if source_map is not None:
        data = source_map.get(path)
        if data is not None:
            return data.decode("utf-8", errors="replace")
    abs_path = getattr(parsed.file_info, "abs_path", None)
    if not abs_path:
        return None
    try:
        with open(abs_path, encoding="utf-8", errors="replace") as f:
            return f.read()
    except OSError:
        return None


def _mark_cpp_entry_point_files(
    parsed_files: dict,
    graph: Any,
    source_map: dict[str, bytes] | None = None,
) -> None:
    """Stamp ``is_entry_point=True`` on TU file nodes matching an entry marker."""
    for path, parsed in parsed_files.items():
        lang = parsed.file_info.language
        if lang not in ("cpp", "c"):
            continue
        src = _read_warmup_source(path, parsed, source_map)
        if src is None:
            continue
        if not any(tok in src for tok in _CPP_ENTRY_MARKERS):
            continue
        node = graph.nodes.get(path)
        if node is not None:
            node["is_entry_point"] = True


_SWIFT_ENTRY_RE = None  # compiled lazily inside _warmup_swift


def _warmup_swift(ctx: ResolverContext) -> None:
    """Stamp ``is_entry_point`` on Swift files carrying an entry attribute.

    ``@main`` / ``@UIApplicationMain`` / ``@NSApplicationMain`` mark the
    process entry type; no call edge ever points at it, so without the
    flag the app's actual starting point reads as unreachable.
    """
    import re

    global _SWIFT_ENTRY_RE
    if _SWIFT_ENTRY_RE is None:
        _SWIFT_ENTRY_RE = re.compile(
            r"^\s*@(?:main|UIApplicationMain|NSApplicationMain)\b", re.MULTILINE
        )

    graph = getattr(ctx, "graph", None)
    parsed_files = getattr(ctx, "parsed_files", None) or {}
    source_map = getattr(ctx, "source_map", None)
    if graph is None:
        return
    for path, parsed in parsed_files.items():
        if parsed.file_info.language != "swift":
            continue
        src = _read_warmup_source(path, parsed, source_map)
        if src is None:
            continue
        if not _SWIFT_ENTRY_RE.search(src):
            continue
        node = graph.nodes.get(path)
        if node is not None:
            node["is_entry_point"] = True


def _warmup_dotnet(ctx: ResolverContext) -> None:
    from .resolvers.dotnet import get_or_build_index

    get_or_build_index(ctx)


def _warmup_go(ctx: ResolverContext) -> None:
    """Build the Go package index and stamp ``is_entry_point`` on every
    ``package main`` file declaring ``func main()``. Go's entry convention
    is semantic, not filename-based — ``cmd/task/task.go`` is as much a
    binary entry as ``cmd/release/main.go``, so the traverser's stem
    heuristic alone misses real binaries.
    """
    from .resolvers.go_workspace import get_or_build_go_index

    index = get_or_build_go_index(ctx)
    graph = getattr(ctx, "graph", None)
    parsed = getattr(ctx, "parsed_files", None) or {}
    for pkg in index.packages.values():
        for path in pkg.main_files:
            # The graph attribute feeds dead-code reachability; the parsed
            # FileInfo flag feeds the exported KG's entry tags and the tour
            # seeds — both surfaces must agree.
            if graph is not None:
                node = graph.nodes.get(path)
                if node is not None:
                    node["is_entry_point"] = True
            pf = parsed.get(path)
            if pf is not None and getattr(pf, "file_info", None) is not None:
                pf.file_info.is_entry_point = True


def _warmup_typescript(ctx: ResolverContext) -> None:
    """Build the TS workspace index and stamp ``is_entry_point`` on every
    source file the workspace's ``package.json`` ``exports`` map resolves
    to. Without this, files reachable only through the package boundary
    (downstream npm consumers) read as ``in_degree==0`` and ship as
    unreachable findings.
    """
    from .resolvers.ts_workspace import (
        find_mdx_import_targets,
        find_npm_script_entry_targets,
        find_vitest_include_targets,
        get_or_build_ts_index,
    )

    index = get_or_build_ts_index(ctx)
    graph = getattr(ctx, "graph", None)
    if graph is None:
        return
    entry_paths: set[str] = set(index.exports_entry_paths)
    # MDX-only consumers (docs sites that import TSX components into
    # ``.mdx``) and custom vitest layouts (``runtime-tests/**``) — both
    # invisible to the TS parser, both real entry points.
    with contextlib.suppress(Exception):
        entry_paths |= find_mdx_import_targets(ctx)
    with contextlib.suppress(Exception):
        entry_paths |= find_vitest_include_targets(ctx)
    # ``package.json`` ``scripts.*`` references: benchmark / bench-runner /
    # rollup-input paths that ship as live code but are never imported
    # by the main entry graph.
    with contextlib.suppress(Exception):
        entry_paths |= find_npm_script_entry_targets(ctx)
    for path in entry_paths:
        node = graph.nodes.get(path)
        if node is None:
            continue
        node["is_entry_point"] = True


_FLUTTER_SHELL_DIRS = ("android/", "ios/", "linux/", "macos/", "windows/", "web/")


def _warmup_dart(ctx: ResolverContext) -> None:
    """Stamp Flutter platform-shell scaffolding as ``is_never_flag``.

    The ``android/`` / ``ios/`` / ``linux/`` / ``macos/`` / ``windows/`` /
    ``web/`` directories next to a ``pubspec.yaml`` are runner shells the
    ``flutter`` tool wires in at build time — no source import reaches
    them, so every MainActivity.kt / my_application.cc / win32_window.cpp
    reads as unreachable otherwise. A pure-Dart package has no shell
    directories, so the prefix test is a no-op there.
    """
    graph = getattr(ctx, "graph", None)
    if graph is None:
        return
    bases = [
        p[: -len("pubspec.yaml")]
        for p in getattr(ctx, "sorted_paths", ())
        if p.endswith("pubspec.yaml")
    ]
    if not bases:
        return
    prefixes = tuple(base + shell for base in bases for shell in _FLUTTER_SHELL_DIRS)
    parsed = getattr(ctx, "parsed_files", None) or {}
    for node_name in list(graph.nodes()):
        s = str(node_name)
        if s.startswith(prefixes):
            nd = graph.nodes.get(node_name)
            if nd is not None:
                nd["is_never_flag"] = True
            continue
        # Flutter flavor entrypoints (``flutter run -t lib/main_staging.dart``)
        # are prefix-shaped, which the traverser's exact/suffix entry-pattern
        # forms can't express — stamp them here instead.
        basename = s.rsplit("/", 1)[-1]
        if basename.startswith("main_") and basename.endswith(".dart"):
            nd = graph.nodes.get(node_name)
            if nd is not None:
                nd["is_entry_point"] = True
            pf = parsed.get(node_name)
            if pf is not None and getattr(pf, "file_info", None) is not None:
                pf.file_info.is_entry_point = True


def _warmup_godot(ctx: ResolverContext) -> None:
    """Stamp Godot engine-invoked entry points, and vendored ``addons/``.

    Two facts about a Godot project that the import graph cannot express, both
    read off its ini manifests.

    **Autoloads, the main scene and an addon's EditorPlugin are entry points.**
    Godot instantiates every ``[autoload]`` singleton before the first scene,
    boots into ``run/main_scene``, and loads an addon's ``plugin.cfg``
    ``script`` when the plugin is enabled. No source imports any of them by
    name (an autoload is reached through a global identifier the engine
    injects), so such a file has inbound edges from the manifest alone, and the
    unreachable-file pass would report the most load-bearing scripts in the
    project. See ``lightweight_imports/godot.py`` for why every import on one
    of these manifests is an execution start.

    Two deliberate imprecisions here. An autoload named by ``uid://`` rather
    than ``res://`` cannot be resolved (the ``.uid`` sidecars are build-cache
    artifacts the spec blocks) and goes unstamped. And a ``plugin.cfg`` script
    is stamped whether or not ``project.godot``'s ``[editor_plugins] enabled=``
    lists it: a checked-in but switched-off plugin is not dead code, it is
    off.

    **``addons/`` is vendored when a Godot project encloses it.** Godot has no
    package manager: a plugin is distributed by copying its ``addons/<name>/``
    tree into the consuming project, so ``addons/`` is a checked-in
    ``node_modules``. Its scripts are a third party's public API, reached by
    the editor or by the plugin's own scenes, and reporting them as dead is
    reporting on code the repo does not own.

    But the *publisher* of a plugin also keeps it in ``addons/``, and there the
    same tree is the entire product. The discriminator implemented here is
    whether a ``project.godot`` sits in an *ancestor* directory of the
    ``addons/`` tree, not whether the repo has one anywhere. On the corpus
    that exempts Pixelorama's 39 vendored scripts and spares dialogic's 264
    first-party ones, which matters because 97% of dialogic *is* ``addons/``.

    **Its known failure mode**, and it is not hypothetical: a publisher that
    ships a demo or test project at the repo root gets its own product
    never-flagged. dialogic escapes only because its single ``project.godot``
    is a CI fixture parked under ``.github/``. Two of the four corpus repos
    have no ``addons/`` at all, so the rule is really evidenced by n=2.

    The alternative rule, vendored unless the project declares a plugin entry
    for it, reaches the same verdict on both corpus repos. It differs only for
    a vendored plugin that is switched *off*, which ``[editor_plugins]
    enabled=`` would not list and which this treats as vendored anyway.
    """
    graph = getattr(ctx, "graph", None)
    if graph is None:
        return

    project_files: list[str] = []
    manifests: list[str] = []
    for p in getattr(ctx, "sorted_paths", ()):
        name = p.rsplit("/", 1)[-1]
        if name == "project.godot":
            project_files.append(p)
            manifests.append(p)
        elif name == "plugin.cfg":
            manifests.append(p)
    if not manifests:
        return

    from .resolvers.gdscript import resolve_gdscript_import

    parsed_files = getattr(ctx, "parsed_files", None) or {}
    for path in manifests:
        parsed = parsed_files.get(path)
        for imp in getattr(parsed, "imports", ()) or ():
            target = resolve_gdscript_import(imp.module_path, path, ctx)
            # An unresolved path comes back as an ``external:`` node the
            # resolver just minted. Flagging that says nothing about a file in
            # this repo, so only in-repo targets are stamped.
            if target is None or target not in ctx.path_set:
                continue
            node = graph.nodes.get(target)
            if node is not None:
                node["is_entry_point"] = True
            # Both, as _warmup_dart and _warmup_go do: the graph attribute is
            # what dead-code reachability reads, but the wiki's entry-point
            # list, the tour and page selection all read FileInfo. Stamping
            # only the first would leave an addon-publisher repo still
            # reporting no entry point anywhere a reader looks.
            target_parsed = parsed_files.get(target)
            if target_parsed is not None and getattr(target_parsed, "file_info", None):
                target_parsed.file_info.is_entry_point = True

    # A project root of "" (project.godot at the repo root) gives "addons/".
    # Keyed on project.godot only: a plugin.cfg is what marks an addon, not
    # what makes it someone else's.
    if not project_files:
        return
    addon_prefixes = tuple(p[: -len("project.godot")] + "addons/" for p in project_files)
    for node_name in list(graph.nodes()):
        if str(node_name).startswith(addon_prefixes):
            nd = graph.nodes.get(node_name)
            if nd is not None:
                nd["is_never_flag"] = True


# Map language tag → (phase-event name, warmup function). The phase
# name shows up in the CLI progress bar and in ``state.json`` timings.
#
# Note: ``typescript`` and ``javascript`` share a single warmup — the
# workspace index is derived from ``package.json`` files and is the
# same for both languages. The dispatcher registers under each tag so
# a JS-only repo still triggers the index build.
_WARMUPS: dict[str, tuple[str, Warmup]] = {
    "java": ("graph.jvm_index", _warmup_jvm),
    "kotlin": ("graph.jvm_index", _warmup_jvm),
    "csharp": ("graph.dotnet_index", _warmup_dotnet),
    "go": ("graph.go_index", _warmup_go),
    "typescript": ("graph.ts_index", _warmup_typescript),
    "javascript": ("graph.ts_index", _warmup_typescript),
    "cpp": ("graph.cpp_index", _warmup_cpp),
    "c": ("graph.cpp_index", _warmup_cpp),
    "swift": ("graph.swift_entry", _warmup_swift),
    "dart": ("graph.dart_shells", _warmup_dart),
    # Registered under both tags: a repo of loose .gd scripts has no scenes,
    # and an addon distributed as scenes plus a project.godot may carry no
    # first-party .gd at all. Shared event name, and the warmup is a no-op
    # without a project.godot, so firing twice is harmless.
    "gdscript": ("graph.godot_project", _warmup_godot),
    "godot_resource": ("graph.godot_project", _warmup_godot),
}


def run_warmups(
    parsed_files: dict[str, ParsedFile],
    ctx: ResolverContext,
    progress: Any | None = None,
) -> None:
    """Run every registered warmup whose language appears in ``parsed_files``.

    Each warmup runs under its own ``on_phase_start`` / ``on_phase_done``
    pair so phase timings attribute the cost to the language rather
    than dropping it into ``graph.imports``.
    """
    present_langs: set[str] = {pf.file_info.language for pf in parsed_files.values()}
    fired_phases: set[str] = set()
    for lang, (phase_name, warmup) in _WARMUPS.items():
        if lang not in present_langs:
            continue
        # Some warmups (TS + JS) share a phase event because they share the
        # underlying index — only fire start/done once per phase name and
        # rely on the warmup's own idempotency for the second invocation.
        if phase_name in fired_phases:
            with contextlib.suppress(Exception):
                warmup(ctx)
            continue
        fired_phases.add(phase_name)
        if progress is not None:
            progress.on_phase_start(phase_name, None)
        with contextlib.suppress(Exception):  # warmup failures must not abort the build
            warmup(ctx)
        if progress is not None:
            done = getattr(progress, "on_phase_done", None)
            if callable(done):
                done(phase_name)
