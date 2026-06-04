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
