"""Heritage / member-read / call edge resolution for :class:`GraphBuilder`.

Each pass reads ``self._parsed_files`` and mutates ``self._graph`` in place,
emitting EXTENDS/IMPLEMENTS, ``reads``, and ``calls`` edges respectively.
"""

from __future__ import annotations

from typing import Any

import structlog

log = structlog.get_logger(__name__)


class ResolveMixin:
    """Symbol-level edge resolution passes run during ``build()``."""

    def _shared_import_maps(self) -> Any:
        """Build the import-name maps once per build; both resolvers share them."""
        maps = getattr(self, "_import_name_maps", None)
        if maps is None:
            from ..import_index import build_import_name_maps

            maps = build_import_name_maps(self._parsed_files)
            self._import_name_maps = maps
        return maps

    def _resolve_heritage(
        self,
        import_targets: dict[str, set[str]],
        progress: Any | None = None,
    ) -> None:
        """Resolve heritage relations and add EXTENDS/IMPLEMENTS edges."""
        from ..heritage_resolver import HeritageResolver

        resolver = HeritageResolver(
            self._parsed_files, import_targets, import_maps=self._shared_import_maps()
        )
        total_resolved = 0

        files_with_heritage = [
            (p, pf) for p, pf in self._parsed_files.items() if pf.heritage
        ]
        if progress:
            progress.on_phase_start("graph.heritage", len(files_with_heritage))
        for path, parsed in files_with_heritage:
            resolved = resolver.resolve_file(path, parsed.heritage)
            for rh in resolved:
                if rh.child_id in self._graph and rh.parent_id in self._graph:
                    if not self._graph.has_edge(rh.child_id, rh.parent_id):
                        self._graph.add_edge(
                            rh.child_id,
                            rh.parent_id,
                            edge_type=rh.edge_type,
                            confidence=rh.confidence,
                        )
                        total_resolved += 1
                    else:
                        existing = self._graph[rh.child_id][rh.parent_id]
                        if rh.confidence > existing.get("confidence", 0):
                            existing["confidence"] = rh.confidence
            if progress:
                progress.on_item_done("graph.heritage")

        if progress:
            _phase_done = getattr(progress, "on_phase_done", None)
            if _phase_done is not None:
                _phase_done("graph.heritage")
        log.info("Heritage edges resolved", total=total_resolved)

    def _resolve_member_reads(self, progress: Any | None = None) -> None:
        """Phase 1c: emit ``reads`` edges for C# property / member access.

        Runs after type-use resolution so the dead-code analyser sees
        member access as evidence of reachability. The pass is C#-only
        today (the lever is largest there); the helper module is set
        up to receive other languages via additional strategies.
        """
        from ..languages.csharp_member_reads import (
            build_csharp_type_to_file,
            collect_csharp_source_texts,
            resolve_csharp_member_reads,
        )

        has_csharp = any(
            pf.file_info.language == "csharp" for pf in self._parsed_files.values()
        )
        if not has_csharp:
            return

        phase = "graph.member_reads"
        if progress:
            progress.on_phase_start(phase, None)
        try:
            cs_texts = collect_csharp_source_texts(self._parsed_files)
            type_to_file = build_csharp_type_to_file(self._parsed_files)
            added = resolve_csharp_member_reads(self._graph, cs_texts, type_to_file)
            log.info("member_read_edges", language="csharp", added=added)
        except Exception as exc:
            log.warning("member_reads_failed", error=str(exc))
        finally:
            if progress:
                done = getattr(progress, "on_phase_done", None)
                if callable(done):
                    done(phase)

    def _resolve_jvm_same_package(self, ctx: Any, progress: Any | None = None) -> None:
        """Emit same-package ``imports`` edges for JVM files.

        JVM languages reference same-package types without an import
        statement, so cohesive packages otherwise produce zero edges
        between sibling files. Conservative text-level scan against the
        JVM workspace index (already built — cached on *ctx* — by the
        import resolution phase).
        """
        from ..languages.jvm_same_package import (
            collect_jvm_source_texts,
            resolve_jvm_same_package_refs,
        )
        from ..resolvers.jvm_workspace import get_or_build_jvm_index

        has_jvm = any(
            pf.file_info.language in ("java", "kotlin", "scala")
            for pf in self._parsed_files.values()
        )
        if not has_jvm:
            return

        phase = "graph.same_package"
        if progress:
            progress.on_phase_start(phase, None)
        try:
            jvm_index = get_or_build_jvm_index(ctx)
            texts = collect_jvm_source_texts(self._parsed_files)
            added = resolve_jvm_same_package_refs(self._graph, jvm_index, texts)
            log.info("same_package_edges", added=added)
        except Exception as exc:
            log.warning("jvm_same_package_failed", error=str(exc))
        finally:
            if progress:
                done = getattr(progress, "on_phase_done", None)
                if callable(done):
                    done(phase)

    def _resolve_csharp_same_namespace(self, ctx: Any, progress: Any | None = None) -> None:
        """Emit same-namespace / global-using ``imports`` edges for C# files.

        C# references same-namespace types with no using directive, and
        ``global using`` / csproj ``<Using>`` items make namespaces visible
        project-wide — both leave cohesive code (and whole test suites)
        looking like zero-edge orphans. Conservative text-level scan, same
        shape as the JVM same-package pass.
        """
        from ..languages.csharp_member_reads import collect_csharp_source_texts
        from ..languages.csharp_same_namespace import (
            resolve_csharp_same_namespace_refs,
        )
        from ..resolvers.dotnet import get_or_build_index

        has_csharp = any(
            pf.file_info.language == "csharp" for pf in self._parsed_files.values()
        )
        if not has_csharp:
            return

        phase = "graph.same_namespace"
        if progress:
            progress.on_phase_start(phase, None)
        try:
            index = get_or_build_index(ctx)
            cs_texts = collect_csharp_source_texts(self._parsed_files)
            repo = getattr(index, "repo_path", None) if index is not None else None
            added = resolve_csharp_same_namespace_refs(
                self._graph, index, cs_texts, repo
            )
            log.info("same_namespace_edges", language="csharp", added=added)
        except Exception as exc:
            log.warning("csharp_same_namespace_failed", error=str(exc))
        finally:
            if progress:
                done = getattr(progress, "on_phase_done", None)
                if callable(done):
                    done(phase)

    def _resolve_ruby_spec_mirrors(self, progress: Any | None = None) -> None:
        """Link rspec files to their subjects by the directory-mirror convention.

        RSpec loads ``spec_helper`` through ``.rspec`` and resolves the
        subject constant at runtime, so a typical spec file contains *no*
        require at all — every ``spec/lib/rack/protection/base_spec.rb``
        reads as a zero-edge orphan. The rspec convention mirrors the
        source tree: ``<root>/spec/<sub>/<name>_spec.rb`` tests
        ``<root>/<sub>/<name>.rb`` (or ``<root>/lib/<sub>/<name>.rb``).
        """
        ruby_files = [
            p
            for p, pf in self._parsed_files.items()
            if pf.file_info.language == "ruby"
        ]
        if not ruby_files:
            return

        phase = "graph.spec_mirrors"
        if progress:
            progress.on_phase_start(phase, None)
        try:
            added = 0
            for p in sorted(ruby_files):
                if not p.endswith("_spec.rb") or "/spec/" not in f"/{p}":
                    continue
                prefix, _, sub = f"/{p}".rpartition("/spec/")
                root = prefix.lstrip("/")
                subject_rel = sub[: -len("_spec.rb")] + ".rb"
                candidates = []
                for mid in ("", "lib/"):
                    joined = "/".join(s for s in (root, mid.rstrip("/"), subject_rel) if s)
                    candidates.append(joined)
                for cand in candidates:
                    if cand == p or cand not in self._parsed_files:
                        continue
                    if not self._graph.has_node(p) or not self._graph.has_node(cand):
                        continue
                    if self._graph.has_edge(p, cand):
                        break
                    self._graph.add_edge(
                        p,
                        cand,
                        edge_type="imports",
                        imported_names=[],
                        hint_source="spec_mirror",
                    )
                    added += 1
                    break
            log.info("spec_mirror_edges", language="ruby", added=added)
        except Exception as exc:
            log.warning("ruby_spec_mirrors_failed", error=str(exc))
        finally:
            if progress:
                done = getattr(progress, "on_phase_done", None)
                if callable(done):
                    done(phase)

    def _resolve_cpp_header_pairs(self, progress: Any | None = None) -> None:
        """Pair C/C++ headers with their same-stem same-dir implementations.

        ``foo.c`` → ``foo.h`` exists via the #include, but nothing ever
        points ``foo.h`` → ``foo.c`` — so a consumer that includes the
        header can never reach the implementation and every ``.c`` whose
        only relationship is "implements its header" reads as orphaned.
        The pairing edge makes BFS transit headers into implementations.
        """
        header_exts = (".h", ".hpp", ".hxx", ".hh", ".h++")
        source_exts = (".c", ".cc", ".cpp", ".cxx", ".c++")

        cpp_files = [
            p
            for p, pf in self._parsed_files.items()
            if pf.file_info.language in ("c", "cpp")
        ]
        if not cpp_files:
            return

        phase = "graph.header_pairs"
        if progress:
            progress.on_phase_start(phase, None)
        try:
            from pathlib import PurePosixPath

            by_dir_stem: dict[tuple[str, str], dict[str, list[str]]] = {}
            for p in cpp_files:
                pp = PurePosixPath(p)
                suffix = pp.suffix.lower()
                if suffix in header_exts:
                    kind = "header"
                elif suffix in source_exts:
                    kind = "source"
                else:
                    continue
                key = (pp.parent.as_posix(), pp.stem.lower())
                by_dir_stem.setdefault(key, {}).setdefault(kind, []).append(p)

            added = 0
            for _key, kinds in sorted(by_dir_stem.items()):
                headers = sorted(kinds.get("header", []))
                sources = sorted(kinds.get("source", []))
                for h in headers:
                    for s in sources:
                        for a, b in ((h, s), (s, h)):
                            if not self._graph.has_node(a) or not self._graph.has_node(b):
                                continue
                            if self._graph.has_edge(a, b):
                                continue
                            self._graph.add_edge(
                                a,
                                b,
                                edge_type="imports",
                                imported_names=[],
                                hint_source="header_source_pair",
                            )
                            added += 1
            log.info("header_pair_edges", added=added)
        except Exception as exc:
            log.warning("cpp_header_pairs_failed", error=str(exc))
        finally:
            if progress:
                done = getattr(progress, "on_phase_done", None)
                if callable(done):
                    done(phase)

    def _resolve_csharp_partials(self, ctx: Any, progress: Any | None = None) -> None:
        """Link C# ``partial`` co-fragments of one type bidirectionally.

        Fragments of a partial class across files are literally one
        class — without these edges the secondary fragment files read as
        disconnected from their own type.
        """
        from ..resolvers.dotnet import get_or_build_index

        has_csharp = any(
            pf.file_info.language == "csharp" for pf in self._parsed_files.values()
        )
        if not has_csharp:
            return

        phase = "graph.partials"
        if progress:
            progress.on_phase_start(phase, None)
        try:
            index = get_or_build_index(ctx)
            added = 0
            if index is not None and index.partial_types:
                repo = index.repo_path
                for fqn, files in sorted(index.partial_types.items()):
                    rels = []
                    for f in files:
                        try:
                            rel = f.resolve().relative_to(repo).as_posix()
                        except (OSError, ValueError):
                            continue
                        if self._graph.has_node(rel):
                            rels.append(rel)
                    if len(rels) < 2:
                        continue
                    local_name = fqn.rsplit(".", 1)[-1]
                    for a in rels:
                        for b in rels:
                            if a == b or self._graph.has_edge(a, b):
                                continue
                            self._graph.add_edge(
                                a,
                                b,
                                edge_type="imports",
                                imported_names=[local_name],
                                hint_source="partial_class",
                            )
                            added += 1
            log.info("partial_class_edges", language="csharp", added=added)
        except Exception as exc:
            log.warning("csharp_partials_failed", error=str(exc))
        finally:
            if progress:
                done = getattr(progress, "on_phase_done", None)
                if callable(done):
                    done(phase)

    def _resolve_swift_same_module(self, ctx: Any, progress: Any | None = None) -> None:
        """Emit same-module ``imports`` edges for Swift files.

        Swift has no intra-module imports by design — every file in an
        SPM target sees every sibling's top-level declarations — so
        targets otherwise read as edge deserts. Conservative text-level
        scan against the per-target declared-type map.
        """
        from ..languages.swift_same_module import (
            collect_swift_source_texts,
            resolve_swift_same_module_refs,
        )
        from ..resolvers.swift_spm import get_or_build_swift_targets

        has_swift = any(
            pf.file_info.language == "swift" for pf in self._parsed_files.values()
        )
        if not has_swift:
            return

        phase = "graph.same_module"
        if progress:
            progress.on_phase_start(phase, None)
        try:
            swift_targets = get_or_build_swift_targets(ctx)
            texts = collect_swift_source_texts(self._parsed_files)
            added = resolve_swift_same_module_refs(self._graph, swift_targets, texts)
            log.info("same_module_edges", language="swift", added=added)
        except Exception as exc:
            log.warning("swift_same_module_failed", error=str(exc))
        finally:
            if progress:
                done = getattr(progress, "on_phase_done", None)
                if callable(done):
                    done(phase)

    def _resolve_fsharp_compile_order(self, ctx: Any, progress: Any | None = None) -> None:
        """Emit fsproj compile-order ``imports`` hint edges for F# files.

        F# compiles project files in fsproj declaration order and a file
        may only reference earlier files — the order is a real dependency
        constraint. Adjacent pairs contribute ``later → earlier`` edges so
        projects whose files rarely ``open`` their own namespaces don't
        read as edge deserts.
        """
        from ..languages.fsharp_compile_order import add_fsharp_compile_order_edges

        has_fsharp = any(
            pf.file_info.language == "fsharp" for pf in self._parsed_files.values()
        )
        if not has_fsharp or ctx.repo_path is None:
            return

        phase = "graph.compile_order"
        if progress:
            progress.on_phase_start(phase, None)
        try:
            added = add_fsharp_compile_order_edges(
                self._graph, ctx.repo_path, prune_nested_git=ctx.prune_nested_git
            )
            log.info("compile_order_edges", language="fsharp", added=added)
        except Exception as exc:
            log.warning("fsharp_compile_order_failed", error=str(exc))
        finally:
            if progress:
                done = getattr(progress, "on_phase_done", None)
                if callable(done):
                    done(phase)

    def _resolve_go_interface_satisfaction(self, progress: Any | None = None) -> None:
        """Emit ``method_implements`` edges for Go structural interface
        satisfaction.

        Go has no nominal ``implements`` clause, so interfaces reached only
        through their concrete implementors look like unreferenced exports.
        This pass connects each concrete type to the interfaces its method
        set satisfies, landing a usage signal on the interface symbol. Runs
        after heritage so the interface / type symbols already exist as nodes.
        """
        from ..languages.go_interface_satisfaction import (
            resolve_go_interface_satisfaction,
        )

        has_go = any(
            pf.file_info.language == "go" for pf in self._parsed_files.values()
        )
        if not has_go:
            return

        phase = "graph.go_interfaces"
        if progress:
            progress.on_phase_start(phase, None)
        try:
            added = resolve_go_interface_satisfaction(self._graph, self._parsed_files)
            log.info("interface_satisfaction_edges", language="go", added=added)
        except Exception as exc:
            log.warning("go_interface_satisfaction_failed", error=str(exc))
        finally:
            if progress:
                done = getattr(progress, "on_phase_done", None)
                if callable(done):
                    done(phase)

    def _resolve_calls(
        self,
        import_targets: dict[str, set[str]],
        progress: Any | None = None,
    ) -> None:
        """Run three-tier call resolution and add CALLS edges to the graph."""
        from ..call_resolver import CallResolver

        resolver = CallResolver(
            self._parsed_files,
            import_targets,
            repo_path=str(self._repo_path) if self._repo_path else None,
            import_maps=self._shared_import_maps(),
        )
        total_resolved = 0

        files_with_calls = [
            (p, pf) for p, pf in self._parsed_files.items() if pf.calls
        ]
        if progress:
            progress.on_phase_start("graph.calls", len(files_with_calls))
        for path, parsed in files_with_calls:
            resolved = resolver.resolve_file(path, parsed.calls)
            for rc in resolved:
                if rc.caller_id in self._graph and rc.callee_id in self._graph:
                    if not self._graph.has_edge(rc.caller_id, rc.callee_id):
                        self._graph.add_edge(
                            rc.caller_id,
                            rc.callee_id,
                            edge_type="calls",
                            confidence=rc.confidence,
                        )
                        total_resolved += 1
                    else:
                        existing = self._graph[rc.caller_id][rc.callee_id]
                        if rc.confidence > existing.get("confidence", 0):
                            existing["confidence"] = rc.confidence
            if progress:
                progress.on_item_done("graph.calls")

        if progress:
            _phase_done = getattr(progress, "on_phase_done", None)
            if _phase_done is not None:
                _phase_done("graph.calls")
        log.info("Call edges resolved", total=total_resolved)
