"""ContextAssembler — converts ParsedFile + graph metrics into template context."""

from __future__ import annotations

from typing import Any

import structlog

from repowise.core.ingestion.models import ParsedFile, RepoStructure, Symbol

from ..categories import file_category
from ..models import GenerationConfig
from .contexts import (
    ApiContractContext,
    ArchitectureDiagramContext,
    FilePageContext,
    InfraPageContext,
    ModulePageContext,
    RepoOverviewContext,
    SccPageContext,
    SymbolSpotlightContext,
    _TopFile,
)
from .graph_intelligence import (
    extract_call_graph,
    extract_community_meta,
    extract_heritage,
)
from .token_budget import (
    estimate_kg_tokens,
    estimate_tokens,
    items_within_budget,
    trim_to_budget,
)

log = structlog.get_logger(__name__)

# Maximum imports to include before truncating
_MAX_IMPORTS = 30
# Maximum top-files to include in repo overview
_MAX_TOP_FILES = 20


# ---------------------------------------------------------------------------
# ContextAssembler
# ---------------------------------------------------------------------------


class ContextAssembler:
    """Assemble Jinja2 template context from ingestion data.

    All public methods return one context dataclass.  The token budget is
    applied inside ``_assemble_with_budget`` — no assembly method exceeds
    ``config.token_budget`` tokens.
    """

    def __init__(self, config: GenerationConfig) -> None:
        self._config = config

    # ------------------------------------------------------------------
    # Token utilities
    # ------------------------------------------------------------------

    def _estimate_tokens(self, text: str) -> int:
        """Estimate token count using the 4-chars-per-token heuristic."""
        return estimate_tokens(text)

    def _trim_to_budget(self, text: str, remaining: int) -> str:
        """Truncate *text* so it fits within *remaining* token budget."""
        return trim_to_budget(text, remaining)

    def _estimate_kg_tokens(self, kg_context: Any) -> int:
        """Estimate token cost for KG context sections in the template."""
        return estimate_kg_tokens(kg_context)

    # ------------------------------------------------------------------
    # File page
    # ------------------------------------------------------------------

    def assemble_file_page(
        self,
        parsed: ParsedFile,
        graph: Any,  # nx.DiGraph
        pagerank: dict[str, float],
        betweenness: dict[str, float],
        community: dict[str, int],
        source_bytes: bytes,
        git_meta: dict | None = None,
        dead_code_findings: list[dict] | None = None,
        page_summaries: dict[str, str] | None = None,
        decision_records: list[dict] | None = None,
        kg_context: Any | None = None,
        symbol_index: dict[str, list[tuple[Any, dict]]] | None = None,
    ) -> FilePageContext:
        """Assemble context for the file_page template.

        *symbol_index* (see :func:`build_symbol_index`) makes the call-graph /
        heritage extraction a dict lookup instead of a full graph-node scan —
        pass it when assembling context for many files against one graph.
        """
        path = parsed.file_info.path
        budget = self._config.token_budget
        used = 0

        # Reserve token budget for KG context when available
        kg_budget_tokens = 800
        if kg_context:
            used += min(kg_budget_tokens, self._estimate_kg_tokens(kg_context))

        # Always include: path + language tag overhead
        used += self._estimate_tokens(path) + 5

        # Separate public and private symbols
        public_syms = [s for s in parsed.symbols if s.visibility == "public"]
        private_syms = [s for s in parsed.symbols if s.visibility != "public"]

        # Build symbol dicts — public first, then private (documented before
        # undocumented), each added only while the running budget allows.
        sig_cost = lambda s: self._estimate_tokens(s.signature or "")  # noqa: E731
        selected_public, used = items_within_budget(public_syms, used, budget, sig_cost)
        private_documented = [s for s in private_syms if s.docstring]
        private_undocumented = [s for s in private_syms if not s.docstring]
        selected_private, used = items_within_budget(
            private_documented + private_undocumented, used, budget, sig_cost
        )
        sym_dicts = [_symbol_to_dict(s) for s in selected_public + selected_private]

        # Imports (truncate to _MAX_IMPORTS)
        raw_imports = [imp.raw_statement for imp in parsed.imports]
        import_list = raw_imports[:_MAX_IMPORTS]
        imports_text = "\n".join(import_list)
        imports_tokens = self._estimate_tokens(imports_text)
        if used + imports_tokens <= budget:
            used += imports_tokens
        else:
            import_list = []

        # Graph edges
        in_edges = list(graph.predecessors(path)) if path in graph else []
        out_edges = list(graph.successors(path)) if path in graph else []
        # Filter out external nodes
        in_edges = [e for e in in_edges if not e.startswith("external:")]
        out_edges = [e for e in out_edges if not e.startswith("external:")]

        # Source snippet — use structural summary for large files
        source_text = source_bytes.decode("utf-8", errors="replace")
        remaining = budget - used
        source_tokens = self._estimate_tokens(source_text)
        threshold = self._config.token_budget * self._config.large_file_source_pct
        if source_tokens > remaining and source_tokens > threshold:
            snippet = self._build_structural_summary(parsed, source_text, remaining)
        else:
            snippet = self._trim_to_budget(source_text, remaining)
        used += self._estimate_tokens(snippet)

        # Dependency summaries from already-completed pages
        dep_summaries: dict[str, str] = {}
        if page_summaries:
            for dep in out_edges:
                if dep in page_summaries:
                    dep_summaries[dep] = page_summaries[dep]

        # Generation depth
        depth = self._select_generation_depth(path, git_meta, pagerank.get(path, 0.0))

        # Graph intelligence: call graph, heritage, community metadata
        call_graph_entries = extract_call_graph(path, graph, symbol_index)
        heritage_entries = extract_heritage(path, graph, symbol_index)
        community_label, community_cohesion = extract_community_meta(path, graph)

        return FilePageContext(
            file_path=path,
            language=parsed.file_info.language,
            docstring=parsed.docstring,
            symbols=sym_dicts,
            imports=import_list,
            exports=parsed.exports,
            file_source_snippet=snippet,
            pagerank_score=pagerank.get(path, 0.0),
            betweenness_score=betweenness.get(path, 0.0),
            community_id=community.get(path, 0),
            dependents=in_edges,
            dependencies=out_edges,
            is_api_contract=parsed.file_info.is_api_contract,
            is_entry_point=parsed.file_info.is_entry_point,
            is_test=parsed.file_info.is_test,
            file_category=file_category(
                path,
                parsed.file_info.language,
                is_config=getattr(parsed.file_info, "is_config", False),
            ),
            parse_errors=parsed.parse_errors,
            estimated_tokens=used,
            git_metadata=git_meta,
            dead_code_findings=dead_code_findings or [],
            depth=depth,
            dependency_summaries=dep_summaries,
            call_graph=call_graph_entries,
            heritage=heritage_entries,
            community_label=community_label,
            community_cohesion=community_cohesion,
            decision_records=decision_records or [],
            kg_layer_name=kg_context.layer_name if kg_context else "",
            kg_layer_id=kg_context.layer_id if kg_context else "",
            kg_layer_description=kg_context.layer_description if kg_context else "",
            kg_layer_role=kg_context.role if kg_context else "",
            kg_neighbors=kg_context.neighbors if kg_context else [],
            kg_tour_step=kg_context.tour_step if kg_context else None,
            kg_tags=kg_context.tags if kg_context else [],
            kg_node_summary=kg_context.node_summary if kg_context else "",
        )

    # ------------------------------------------------------------------
    # Symbol spotlight
    # ------------------------------------------------------------------

    def assemble_symbol_spotlight(
        self,
        symbol: Symbol,
        parsed: ParsedFile,
        pagerank: dict[str, float],
        graph: Any,  # nx.DiGraph
        source_bytes: bytes = b"",
    ) -> SymbolSpotlightContext:
        """Assemble context for the symbol_spotlight template."""
        path = parsed.file_info.path
        # Callers = files that import the containing file (in-edges)
        if path in graph:
            callers = [e for e in graph.predecessors(path) if not e.startswith("external:")]
        else:
            callers = []

        # Extract source body for the symbol
        source_body = None
        if source_bytes and symbol.start_line and symbol.end_line:
            lines = source_bytes.decode("utf-8", errors="replace").splitlines()
            body_lines = lines[symbol.start_line - 1 : symbol.end_line]
            body = "\n".join(body_lines)
            if len(body) > 8000:
                body = body[:8000] + "\n...[truncated]"
            source_body = body

        return SymbolSpotlightContext(
            symbol_name=symbol.name,
            qualified_name=symbol.qualified_name,
            kind=symbol.kind,
            signature=symbol.signature,
            docstring=symbol.docstring,
            file_path=path,
            decorators=symbol.decorators,
            is_async=symbol.is_async,
            complexity_estimate=symbol.complexity_estimate,
            callers=callers,
            source_body=source_body,
        )

    # ------------------------------------------------------------------
    # Module page
    # ------------------------------------------------------------------

    def assemble_module_page(
        self,
        module_path: str,
        language: str,
        file_contexts: list[FilePageContext],
        graph: Any,  # nx.DiGraph
        page_summaries: dict[str, str] | None = None,
        git_meta_map: dict[str, dict] | None = None,
        decision_records: list[dict] | None = None,
        dead_code_findings: list[dict] | None = None,
        external_systems: list[dict] | None = None,
        community_label: str | None = None,
        community_cohesion: float | None = None,
    ) -> ModulePageContext:
        """Assemble context for the module_page template."""
        total_symbols = sum(len(fc.symbols) for fc in file_contexts)
        public_symbols = sum(
            sum(1 for s in fc.symbols if s.get("visibility") == "public") for fc in file_contexts
        )
        entry_points = [fc.file_path for fc in file_contexts if fc.is_entry_point]
        files = [fc.file_path for fc in file_contexts]

        # Aggregate dependencies/dependents across all files in module
        all_deps: set[str] = set()
        all_dependents: set[str] = set()
        for fc in file_contexts:
            all_deps.update(fc.dependencies)
            all_dependents.update(fc.dependents)
        # Remove intra-module edges
        all_deps -= set(files)
        all_dependents -= set(files)

        pagerank_mean = 0.0
        if file_contexts:
            pagerank_mean = sum(fc.pagerank_score for fc in file_contexts) / len(file_contexts)

        # File summaries from completed pages (enriches module docs with what each file does)
        file_summaries: dict[str, str] = {}
        if page_summaries:
            for fp in files:
                if fp in page_summaries:
                    file_summaries[fp] = page_summaries[fp][:200]

        # Community info: prefer caller-supplied label (community-grouped
        # module pages already know their label), else derive the dominant
        # label across files.
        if community_label is None:
            community_label = ""
            labels = [fc.community_label for fc in file_contexts if fc.community_label]
            if labels:
                from collections import Counter

                community_label = Counter(labels).most_common(1)[0][0]
        if community_cohesion is None:
            community_cohesion = 0.0
            cohesions = [fc.community_cohesion for fc in file_contexts if fc.community_cohesion > 0]
            if cohesions:
                community_cohesion = sum(cohesions) / len(cohesions)

        # Key files inside the module by PageRank, with a short summary.
        ranked = sorted(file_contexts, key=lambda fc: fc.pagerank_score, reverse=True)[:10]
        key_files = [
            {
                "path": fc.file_path,
                "pagerank": round(fc.pagerank_score, 4),
                "summary": (file_summaries.get(fc.file_path) or "").strip()[:200],
                "is_entry_point": fc.is_entry_point,
            }
            for fc in ranked
        ]

        # Aggregate ownership from git metadata: who maintains the most
        # files in this module.
        top_owners: list[dict] = []
        if git_meta_map:
            from collections import Counter

            owner_counts: Counter[str] = Counter()
            for fp in files:
                meta = git_meta_map.get(fp)
                if meta and meta.get("primary_owner_name"):
                    owner_counts[meta["primary_owner_name"]] += 1
            top_owners = [
                {"name": name, "file_count": count} for name, count in owner_counts.most_common(3)
            ]

        # Key classes: collect classes with heritage info from file contexts
        key_classes: list[dict] = []
        for fc in file_contexts:
            for h in fc.heritage[:5]:  # cap per file
                key_classes.append(h)
        key_classes = key_classes[:10]  # cap total

        return ModulePageContext(
            module_path=module_path,
            language=language,
            total_symbols=total_symbols,
            public_symbols=public_symbols,
            entry_points=entry_points,
            dependencies=sorted(all_deps),
            dependents=sorted(all_dependents),
            pagerank_mean=pagerank_mean,
            files=files,
            file_summaries=file_summaries,
            community_label=community_label,
            community_cohesion=community_cohesion,
            key_classes=key_classes,
            decision_records=decision_records or [],
            dead_code_findings=dead_code_findings or [],
            external_systems=external_systems or [],
            key_files=key_files,
            top_owners=top_owners,
        )

    # ------------------------------------------------------------------
    # SCC page
    # ------------------------------------------------------------------

    def assemble_scc_page(
        self,
        scc_id: str,
        scc_files: list[str],
        file_contexts: list[FilePageContext],
    ) -> SccPageContext:
        """Assemble context for the scc_page template."""
        cycle_parts = " → ".join(scc_files)
        cycle_description = f"Circular dependency cycle: {cycle_parts}"
        total_symbols = sum(len(fc.symbols) for fc in file_contexts)

        scc_set = set(scc_files)
        member_symbols = []
        for fc in file_contexts:
            pub = [s for s in fc.symbols if s.get("visibility") == "public"][:5]
            member_symbols.append({"file_path": fc.file_path, "symbols": pub})

        cross_imports = [
            {"from": fc.file_path, "to": dep}
            for fc in file_contexts
            for dep in fc.dependencies
            if dep in scc_set and dep != fc.file_path
        ]

        return SccPageContext(
            scc_id=scc_id,
            files=scc_files,
            cycle_description=cycle_description,
            total_symbols=total_symbols,
            member_symbols=member_symbols,
            cross_imports=cross_imports,
        )

    # ------------------------------------------------------------------
    # Repo overview
    # ------------------------------------------------------------------

    def assemble_repo_overview(
        self,
        repo_structure: RepoStructure,
        pagerank: dict[str, float],
        sccs: list[Any],  # list[frozenset[str]]
        community: dict[str, int],
        graph_builder: Any | None = None,
        external_systems: list[dict] | None = None,
        decision_records: list[dict] | None = None,
    ) -> RepoOverviewContext:
        """Assemble context for the repo_overview template."""
        # Top files sorted by PageRank descending
        sorted_pr = sorted(pagerank.items(), key=lambda x: x[1], reverse=True)
        top_files = [
            _TopFile(path=p, score=s)
            for p, s in sorted_pr[:_MAX_TOP_FILES]
            if not p.startswith("external:")
        ]

        # SCCs with len > 1 are true circular deps
        circular_count = sum(1 for scc in sccs if len(scc) > 1)

        # Community metadata from graph builder
        communities_list: list[dict] = []
        execution_flows_list: list[dict] = []
        if graph_builder is not None:
            try:
                for ci in graph_builder.community_info():
                    communities_list.append(
                        {
                            "id": ci.id,
                            "label": ci.label,
                            "size": ci.size,
                            "cohesion": round(ci.cohesion, 2),
                        }
                    )
                communities_list.sort(key=lambda c: c["size"], reverse=True)
                communities_list = communities_list[:10]
            except Exception:
                pass

            try:
                flow_report = graph_builder.execution_flows()
                if flow_report and hasattr(flow_report, "flows"):
                    for flow in flow_report.flows[:5]:
                        execution_flows_list.append(
                            {
                                "entry_point": flow.entry_point,
                                "score": round(flow.score, 3),
                                "trace_length": len(flow.trace) if hasattr(flow, "trace") else 0,
                            }
                        )
            except Exception:
                pass

        return RepoOverviewContext(
            repo_name=getattr(repo_structure, "name", "repo"),
            is_monorepo=repo_structure.is_monorepo,
            packages=repo_structure.packages,
            language_distribution=repo_structure.root_language_distribution,
            total_files=repo_structure.total_files,
            total_loc=repo_structure.total_loc,
            entry_points=repo_structure.entry_points,
            top_files_by_pagerank=top_files,
            circular_dependency_count=circular_count,
            communities=communities_list,
            execution_flows=execution_flows_list,
            external_systems=external_systems or [],
            decision_records=decision_records or [],
        )

    # ------------------------------------------------------------------
    # Architecture diagram
    # ------------------------------------------------------------------

    def assemble_architecture_diagram(
        self,
        graph: Any,  # nx.DiGraph
        pagerank: dict[str, float],
        community: dict[str, int],
        sccs: list[Any],  # list[frozenset[str]]
        repo_name: str,
    ) -> ArchitectureDiagramContext:
        """Assemble context for the architecture_diagram template."""
        max_diagram_nodes = 50
        max_diagram_edges = 200

        # Top-N nodes by PageRank (exclude external nodes)
        top_nodes = set(
            p
            for p, _ in sorted(pagerank.items(), key=lambda x: x[1], reverse=True)[
                :max_diagram_nodes
            ]
            if not str(p).startswith("external:")
        )
        nodes = sorted(top_nodes)

        # Only edges between selected nodes
        edges = [(src, dst) for src, dst in graph.edges() if src in top_nodes and dst in top_nodes][
            :max_diagram_edges
        ]

        # Community → members mapping (top-10 communities, cap members to 5)
        raw_communities: dict[int, list[str]] = {}
        for path, cid in community.items():
            if not path.startswith("external:"):
                raw_communities.setdefault(cid, []).append(path)
        comm_sorted = sorted(raw_communities.items(), key=lambda x: len(x[1]), reverse=True)[:10]
        communities: dict[int, list[str]] = {cid: members[:5] for cid, members in comm_sorted}

        # SCC groups (only non-singleton)
        scc_groups = [sorted(scc) for scc in sccs if len(scc) > 1]

        return ArchitectureDiagramContext(
            repo_name=repo_name,
            nodes=nodes,
            edges=edges,
            communities=communities,
            scc_groups=scc_groups,
        )

    # ------------------------------------------------------------------
    # API contract
    # ------------------------------------------------------------------

    def assemble_api_contract(
        self,
        parsed: ParsedFile,
        source_bytes: bytes,
    ) -> ApiContractContext:
        """Assemble context for the api_contract template."""
        source_text = source_bytes.decode("utf-8", errors="replace")
        remaining = self._config.token_budget
        raw_content = self._trim_to_budget(source_text, remaining)

        # Extract endpoints/schemas from metadata if available
        endpoints: list[str] = []
        schemas: list[str] = []
        for sym in parsed.symbols:
            if sym.kind in ("function", "method"):
                endpoints.append(sym.signature)
            elif sym.kind in ("class", "interface", "struct"):
                schemas.append(sym.name)

        return ApiContractContext(
            file_path=parsed.file_info.path,
            language=parsed.file_info.language,
            raw_content=raw_content,
            endpoints=endpoints,
            schemas=schemas,
        )

    # ------------------------------------------------------------------
    # Infra page
    # ------------------------------------------------------------------

    def assemble_infra_page(
        self,
        parsed: ParsedFile,
        source_bytes: bytes,
    ) -> InfraPageContext:
        """Assemble context for the infra_page template."""
        source_text = source_bytes.decode("utf-8", errors="replace")
        remaining = self._config.token_budget
        raw_content = self._trim_to_budget(source_text, remaining)

        targets = [sym.name for sym in parsed.symbols]

        return InfraPageContext(
            file_path=parsed.file_info.path,
            language=parsed.file_info.language,
            raw_content=raw_content,
            targets=targets,
        )

    # ------------------------------------------------------------------
    # Structural summary for large files (Phase 9 C1)
    # ------------------------------------------------------------------

    def _build_structural_summary(self, parsed: ParsedFile, source_text: str, budget: int) -> str:
        """Build a structural outline for large files instead of raw truncation.

        Includes full body for the 3 most complex symbols; signature-only for rest.
        """
        lines = source_text.splitlines()
        parts = ["[Large file — structural summary mode]"]
        top3_complex = {
            s.name
            for s in sorted(parsed.symbols, key=lambda s: s.complexity_estimate, reverse=True)[:3]
        }

        for sym in parsed.symbols:
            if sym.start_line and sym.end_line and sym.name in top3_complex:
                body = "\n".join(lines[sym.start_line - 1 : sym.end_line])
                parts.append(
                    f"\n# {sym.name} (full body, complexity={sym.complexity_estimate})\n{body}"
                )
            else:
                parts.append(f"# {sym.signature or sym.name}")
            if self._estimate_tokens("\n".join(parts)) >= budget:
                parts.append("...[remaining symbols omitted]")
                break

        return self._trim_to_budget("\n".join(parts), budget)

    # ------------------------------------------------------------------
    # Generation depth selection (Phase 5.5)
    # ------------------------------------------------------------------

    def _select_generation_depth(
        self,
        file_path: str,
        git_meta: dict | None,
        pagerank_score: float,
        config_depth: str = "standard",
    ) -> str:
        """Select generation depth based on git metadata.

        Upgrade to "thorough" if: hotspot, >100 commits with >10 in 90d,
          >=8 significant commits, or has co-change partners.
        Downgrade to "minimal" if: stable AND pagerank < 0.3 AND commit_count < 5.
        """
        if git_meta is None:
            return config_depth

        import json as _json

        # Upgrade conditions
        if git_meta.get("is_hotspot", False):
            return "thorough"

        commit_total = git_meta.get("commit_count_total", 0)
        commit_90d = git_meta.get("commit_count_90d", 0)
        if commit_total > 100 and commit_90d > 10:
            return "thorough"

        sig_json = git_meta.get("significant_commits_json", "[]")
        try:
            sig_commits = _json.loads(sig_json) if isinstance(sig_json, str) else sig_json
        except Exception:
            sig_commits = []
        if len(sig_commits) >= 8:
            return "thorough"

        co_json = git_meta.get("co_change_partners_json", "[]")
        try:
            co_partners = _json.loads(co_json) if isinstance(co_json, str) else co_json
        except Exception:
            co_partners = []
        if co_partners:
            return "thorough"

        # Downgrade conditions
        if git_meta.get("is_stable", False) and pagerank_score < 0.3 and commit_total < 5:
            return "minimal"

        return config_depth

    # ------------------------------------------------------------------
    # Update context assembly (Phase 5.5)
    # ------------------------------------------------------------------

    def assemble_update_context(
        self,
        parsed: ParsedFile,
        graph: Any,
        pagerank: dict[str, float],
        betweenness: dict[str, float],
        community: dict[str, int],
        source_bytes: bytes,
        trigger_commit_sha: str | None = None,
        trigger_commit_message: str | None = None,
        diff_text: str | None = None,
        git_meta: dict | None = None,
    ) -> FilePageContext:
        """Assemble context for maintenance regeneration using trigger commit + diff."""
        ctx = self.assemble_file_page(
            parsed,
            graph,
            pagerank,
            betweenness,
            community,
            source_bytes,
            git_meta=git_meta,
        )
        # Enrich with trigger context (stored in rag_context for now)
        if trigger_commit_sha:
            ctx.rag_context.append(
                f"Trigger commit: {trigger_commit_sha}"
                + (f" — {trigger_commit_message}" if trigger_commit_message else "")
            )
        if diff_text:
            trimmed_diff = self._trim_to_budget(diff_text, 1000)
            ctx.rag_context.append(f"Diff:\n{trimmed_diff}")
        return ctx


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _symbol_to_dict(symbol: Symbol) -> dict[str, Any]:
    """Convert a Symbol to a plain dict for template rendering."""
    return {
        "name": symbol.name,
        "qualified_name": symbol.qualified_name,
        "kind": symbol.kind,
        "signature": symbol.signature,
        "docstring": symbol.docstring,
        "visibility": symbol.visibility,
        "is_async": symbol.is_async,
        "complexity_estimate": symbol.complexity_estimate,
        "decorators": symbol.decorators,
        "parent_name": symbol.parent_name,
        "start_line": symbol.start_line,
        "end_line": symbol.end_line,
    }
