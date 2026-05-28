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

from collections.abc import Callable
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .models import ParsedFile
    from .resolvers import ResolverContext


# A warmup receives the resolver context and returns nothing. It may
# cache its result on ``ctx`` (the resolvers already use a per-context
# attribute cache); the dispatcher does not inspect the return value.
Warmup = Callable[["ResolverContext"], None]


def _warmup_jvm(ctx: "ResolverContext") -> None:
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


def _warmup_cpp(ctx: "ResolverContext") -> None:
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
            if not path.endswith((".h", ".hpp", ".hxx", ".hh", ".h++", ".inc",
                                  ".c", ".cc", ".cpp", ".cxx", ".c++")):
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

    _mark_cpp_entry_point_files(parsed_files, graph)


# Tokens whose presence means the surrounding TU wires itself into a
# runtime registry at static-init time. Every match marks the file node
# as an entry point so the dead-code analyzer treats it as live.
_CPP_ENTRY_MARKERS = (
    "PYBIND11_MODULE",
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


def _mark_cpp_entry_point_files(parsed_files: dict, graph: Any) -> None:
    """Stamp ``is_entry_point=True`` on TU file nodes matching an entry marker."""
    for path, parsed in parsed_files.items():
        lang = parsed.file_info.language
        if lang not in ("cpp", "c"):
            continue
        # The parser already loaded the source; reuse it via the
        # ParsedFile (avoids a second filesystem read).
        src = getattr(parsed, "source", None) or getattr(parsed.file_info, "source", None)
        if src is None:
            # ParsedFile doesn't always carry the source — fall back to disk.
            abs_path = getattr(parsed.file_info, "abs_path", None)
            if not abs_path:
                continue
            try:
                with open(abs_path, "r", encoding="utf-8", errors="replace") as f:
                    src = f.read()
            except OSError:
                continue
        if not any(tok in src for tok in _CPP_ENTRY_MARKERS):
            continue
        node = graph.nodes.get(path)
        if node is not None:
            node["is_entry_point"] = True


def _warmup_dotnet(ctx: "ResolverContext") -> None:
    from .resolvers.dotnet import get_or_build_index

    get_or_build_index(ctx)


def _warmup_go(ctx: "ResolverContext") -> None:
    from .resolvers.go_workspace import get_or_build_go_index

    get_or_build_go_index(ctx)


def _warmup_typescript(ctx: "ResolverContext") -> None:
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
    try:
        entry_paths |= find_mdx_import_targets(ctx)
    except Exception:
        pass
    try:
        entry_paths |= find_vitest_include_targets(ctx)
    except Exception:
        pass
    # ``package.json`` ``scripts.*`` references: benchmark / bench-runner /
    # rollup-input paths that ship as live code but are never imported
    # by the main entry graph.
    try:
        entry_paths |= find_npm_script_entry_targets(ctx)
    except Exception:
        pass
    for path in entry_paths:
        node = graph.nodes.get(path)
        if node is None:
            continue
        node["is_entry_point"] = True


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
}


def run_warmups(
    parsed_files: dict[str, "ParsedFile"],
    ctx: "ResolverContext",
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
            try:
                warmup(ctx)
            except Exception:
                pass
            continue
        fired_phases.add(phase_name)
        if progress is not None:
            progress.on_phase_start(phase_name, None)
        try:
            warmup(ctx)
        except Exception:  # warmup failures must not abort the build
            pass
        if progress is not None:
            done = getattr(progress, "on_phase_done", None)
            if callable(done):
                done(phase_name)
