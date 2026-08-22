"""Heritage / member-read / call edge resolution for :class:`GraphBuilder`.

Each pass reads ``self._parsed_files`` and mutates ``self._graph`` in place,
emitting EXTENDS/IMPLEMENTS, ``reads``, and ``calls`` edges respectively.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any

import structlog

from ..languages.specs.cpp import INCLUDE_FRAGMENT_EXTENSIONS

log = structlog.get_logger(__name__)

#: Lowest call-resolution confidence a ``references`` edge may be built from.
#: Set above the resolver's global-unique tier (0.50), which binds a name to
#: the only symbol that carries it anywhere in the repo. That tier is a guess
#: even for a call; for a bare identifier it bound ordinary words to unrelated
#: files. See ``_add_reference_edges``.
_MIN_REFERENCE_CONFIDENCE = 0.85

#: Symbol kinds a resolved reference may land on, by how the name was spelled.
#: See ``_add_reference_edges`` for why a bare name is restricted to functions.
_BARE_REFERENCE_KINDS = frozenset({"function"})
_QUALIFIED_REFERENCE_KINDS = frozenset({"function", "method"})


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
            cs_texts = collect_csharp_source_texts(self._parsed_files, self._source_map)
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
            texts = collect_jvm_source_texts(self._parsed_files, self._source_map)
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
            cs_texts = collect_csharp_source_texts(self._parsed_files, self._source_map)
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
        # An include fragment pairs as the implementation side: ``vector.inl``
        # holds what ``vector.h`` declares, which is the relationship this edge
        # exists to carry. The reachability pass groups fragments with headers
        # instead, because the question there is "who imports this", and a
        # fragment has no importer of its own either. Different questions.
        source_exts = (
            ".c",
            ".cc",
            ".cpp",
            ".cxx",
            ".c++",
            *sorted(INCLUDE_FRAGMENT_EXTENSIONS),
        )

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
            texts = collect_swift_source_texts(self._parsed_files, self._source_map)
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

    def _resolve_override_dispatch(self, progress: Any | None = None) -> None:
        """Emit ``dispatches_to`` edges from base methods to implementations.

        Runs last: it reads the heritage edges Phase 2 resolved and the
        ``has_method`` edges ``add_file`` laid down, and nothing else.
        """
        from ..dispatch_edges import resolve_override_dispatch

        phase = "graph.dispatch"
        if progress:
            progress.on_phase_start(phase, None)
        try:
            resolve_override_dispatch(self._graph)
        except Exception as exc:
            log.warning("override_dispatch_failed", error=str(exc))
        finally:
            if progress:
                done = getattr(progress, "on_phase_done", None)
                if callable(done):
                    done(phase)

    def _heritage_parents(self) -> dict[str, set[str]]:
        """``{type symbol id: parent type symbol ids}`` from the built graph.

        Symbol ids, not type names: a name-keyed map unions the parents of
        every same-named class in the repo and reaches ancestors that are not
        this class's.
        """
        parents: dict[str, set[str]] = defaultdict(set)
        for child, parent, data in self._graph.edges(data=True):
            if data.get("edge_type") not in ("extends", "implements"):
                continue
            if (
                self._graph.nodes[child].get("node_type") == "symbol"
                and self._graph.nodes[parent].get("node_type") == "symbol"
            ):
                parents[child].add(parent)
        return parents

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
            heritage_parents=self._heritage_parents(),
        )

        # Record which C/C++ declarations were paired with a definition. The
        # dead-code pass suppresses a declaration only when one was found: a
        # prototype whose body exists nowhere in the repo is real dead code,
        # and the header that declares it is the only place left to report it.
        for decl_id, def_id in resolver.declaration_definitions.items():
            if decl_id in self._graph:
                self._graph.nodes[decl_id]["defined_by"] = def_id

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
                            resolution_origin=rc.origin,
                            call_lines=[rc.line],
                        )
                        total_resolved += 1
                    else:
                        # Several call sites collapse onto one edge; the
                        # strongest wins, and the origin has to follow the
                        # confidence it explains.
                        existing = self._graph[rc.caller_id][rc.callee_id]
                        lines = existing.setdefault("call_lines", [])
                        if rc.line not in lines:
                            lines.append(rc.line)
                            lines.sort()
                        if rc.confidence > existing.get("confidence", 0):
                            existing["confidence"] = rc.confidence
                            existing["resolution_origin"] = rc.origin
            if progress:
                progress.on_item_done("graph.calls")

        if progress:
            _phase_done = getattr(progress, "on_phase_done", None)
            if _phase_done is not None:
                _phase_done("graph.calls")
        log.info("Call edges resolved", total=total_resolved)

        self._add_reference_edges(resolver)

    def _add_reference_edges(self, resolver: Any) -> None:
        """Emit ``references`` edges for functions named but never called.

        Shares the ``CallResolver`` built for calls: resolving "which symbol
        does this name mean" is the same problem and the same three tiers, and
        building a second index over every parsed file would double that cost
        for nothing.

        **What may be named depends on how it was written, not on the
        language.** A receiver-less name produces an edge only to a free
        function: a bare identifier cannot name a member in the languages that
        spell a reference that way, so a plain name resolving to a method is a
        collision rather than a reference, and C++ names its getters exactly
        like the locals that feed them. Measured on leveldb, admitting methods
        there turned ``value``, ``status``, ``level``, ``key`` and ``offset``
        into fifteen edges, every one of them wrong.

        A name that arrived with a receiver went through an operator only a
        callable accepts — ``Foo::bar``, ``pkg.Handler`` in argument position —
        so a method is the expected target and refusing one would discard the
        whole idiom.

        A ``calls`` edge already covering the same pair wins and is left alone:
        it is the stronger claim, and downgrading it here would lose the fact
        that the function is genuinely invoked.

        The confidence floor drops the resolver's last tier, which fires when a
        name happens to be globally unique. That is already a guess for a call;
        for a bare identifier it reached across the repo to bind common words
        like ``output`` to an unrelated file's function. A real dispatch table
        names something in its own file or in a header it includes, which the
        earlier tiers cover.
        """
        total = 0
        for path, parsed in self._parsed_files.items():
            if not parsed.references:
                continue
            bare = [r for r in parsed.references if not r.receiver_name]
            qualified = [r for r in parsed.references if r.receiver_name]
            batches = (
                (bare, _BARE_REFERENCE_KINDS),
                (qualified, _QUALIFIED_REFERENCE_KINDS),
            )
            for sites, allowed_kinds in batches:
                if not sites:
                    continue
                for rc in resolver.resolve_file(path, sites):
                    if rc.confidence < _MIN_REFERENCE_CONFIDENCE:
                        continue
                    if rc.caller_id not in self._graph or rc.callee_id not in self._graph:
                        continue
                    if self._graph.nodes[rc.callee_id].get("kind") not in allowed_kinds:
                        continue
                    if self._graph.has_edge(rc.caller_id, rc.callee_id):
                        continue
                    self._graph.add_edge(
                        rc.caller_id,
                        rc.callee_id,
                        edge_type="references",
                        confidence=rc.confidence,
                        resolution_origin=rc.origin,
                    )
                    total += 1
        log.info("Reference edges resolved", total=total)
