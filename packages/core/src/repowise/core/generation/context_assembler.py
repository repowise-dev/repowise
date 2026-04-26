"""Context assembler for the repowise generation engine.

ContextAssembler converts ParsedFile objects and graph metrics into context
dataclasses that are passed to Jinja2 templates as ``ctx``.

Token budget algorithm (applied per file):
    1. Always include: file path, language, symbol signatures (public first)
    2. If budget allows: symbol docstrings
    3. If budget allows: imports list (truncated to 30 if needed)
    4. If budget allows: file_source_snippet (trimmed to fit remaining)
    5. rag_context: always [] in Phase 3 (Phase 4 ChromaDB stub)

Token estimate: len(text) // 4 (no tiktoken dependency).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import structlog

from repowise.core.ingestion.models import ParsedFile, RepoStructure, Symbol

from .models import GenerationConfig

log = structlog.get_logger(__name__)

# Maximum imports to include before truncating
_MAX_IMPORTS = 30
# Maximum top-files to include in repo overview
_MAX_TOP_FILES = 20


# ---------------------------------------------------------------------------
# Context dataclasses — one per template
# ---------------------------------------------------------------------------


@dataclass
class FilePageContext:
    file_path: str
    language: str
    docstring: str | None
    symbols: list[dict[str, Any]]
    imports: list[str]
    exports: list[str]
    file_source_snippet: str
    pagerank_score: float
    betweenness_score: float
    community_id: int
    dependents: list[str]
    dependencies: list[str]
    is_api_contract: bool
    is_entry_point: bool
    is_test: bool
    parse_errors: list[str]
    estimated_tokens: int
    rag_context: list[str] = field(default_factory=list)
    git_metadata: dict | None = None
    co_change_pages: list[dict] = field(default_factory=list)
    dead_code_findings: list[dict] = field(default_factory=list)
    depth: str = "standard"
    dependency_summaries: dict[str, str] = field(default_factory=dict)
    # Graph intelligence (Phase 5 enrichment)
    call_graph: list[dict] = field(default_factory=list)
    heritage: list[dict] = field(default_factory=list)
    community_label: str = ""
    community_cohesion: float = 0.0


@dataclass
class SymbolSpotlightContext:
    symbol_name: str
    qualified_name: str
    kind: str
    signature: str
    docstring: str | None
    file_path: str
    decorators: list[str]
    is_async: bool
    complexity_estimate: int
    callers: list[str]
    source_body: str | None = None


@dataclass
class ModulePageContext:
    module_path: str
    language: str
    total_symbols: int
    public_symbols: int
    entry_points: list[str]
    dependencies: list[str]
    dependents: list[str]
    pagerank_mean: float
    files: list[str]
    # Graph intelligence enrichment
    file_summaries: dict[str, str] = field(default_factory=dict)
    community_label: str = ""
    community_cohesion: float = 0.0
    key_classes: list[dict] = field(default_factory=list)


@dataclass
class SccPageContext:
    scc_id: str
    files: list[str]
    cycle_description: str
    total_symbols: int
    member_symbols: list[dict] = field(default_factory=list)
    # [{"file_path": str, "symbols": [{"name": str, "signature": str, "docstring": str}]}]
    cross_imports: list[dict] = field(default_factory=list)
    # [{"from": str, "to": str}]


@dataclass
class _TopFile:
    """Helper for repo overview top-files list."""

    path: str
    score: float


@dataclass
class RepoOverviewContext:
    repo_name: str
    is_monorepo: bool
    packages: list[Any]  # PackageInfo objects
    language_distribution: dict[str, float]
    total_files: int
    total_loc: int
    entry_points: list[str]
    top_files_by_pagerank: list[_TopFile]
    circular_dependency_count: int
    # Graph intelligence enrichment
    communities: list[dict] = field(default_factory=list)
    execution_flows: list[dict] = field(default_factory=list)


@dataclass
class ArchitectureDiagramContext:
    repo_name: str
    nodes: list[str]
    edges: list[tuple[str, str]]
    communities: dict[int, list[str]]
    scc_groups: list[list[str]]


@dataclass
class ApiContractContext:
    file_path: str
    language: str
    raw_content: str
    endpoints: list[str]
    schemas: list[str]


@dataclass
class InfraPageContext:
    file_path: str
    language: str
    raw_content: str
    targets: list[str]


@dataclass
class DiffSummaryContext:
    from_ref: str
    to_ref: str
    added_files: list[str]
    deleted_files: list[str]
    modified_files: list[str]
    symbol_diffs: list[Any]
    affected_page_ids: list[str]
    trigger_commit_sha: str | None = None
    trigger_commit_message: str | None = None
    trigger_commit_author: str | None = None
    diff_text: str | None = None


@dataclass
class CrossPackageContext:
    source_package: str
    target_package: str
    coupling_strength: int  # number of boundary files
    used_symbols: list[str]  # public symbols from target used by source
    boundary_files: list[str]  # files in source that import from target
    source_pagerank_mean: float
    target_pagerank_mean: float


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
        return len(text) // 4

    def _trim_to_budget(self, text: str, remaining: int) -> str:
        """Truncate *text* so it fits within *remaining* token budget.

        Appends ``"...[truncated]"`` when truncated.  The suffix is included
        in the budget calculation so the result never exceeds *remaining* tokens.
        """
        if self._estimate_tokens(text) <= remaining:
            return text
        suffix = "...[truncated]"
        suffix_tokens = self._estimate_tokens(suffix)
        max_chars = (remaining - suffix_tokens) * 4
        if max_chars <= 0:
            return suffix if remaining > 0 else ""
        return text[:max_chars] + suffix

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
    ) -> FilePageContext:
        """Assemble context for the file_page template."""
        path = parsed.file_info.path
        budget = self._config.token_budget
        used = 0

        # Always include: path + language tag overhead
        used += self._estimate_tokens(path) + 5

        # Separate public and private symbols
        public_syms = [s for s in parsed.symbols if s.visibility == "public"]
        private_syms = [s for s in parsed.symbols if s.visibility != "public"]

        # Build symbol dicts — public first, add private if budget allows
        selected_symbols: list[Symbol] = []
        for sym in public_syms:
            sym_tokens = self._estimate_tokens(sym.signature or "")
            if used + sym_tokens < budget:
                selected_symbols.append(sym)
                used += sym_tokens
            else:
                break

        # Private symbols — add documented ones first, then undocumented
        private_documented = [s for s in private_syms if s.docstring]
        private_undocumented = [s for s in private_syms if not s.docstring]
        for sym in private_documented + private_undocumented:
            sym_tokens = self._estimate_tokens(sym.signature or "")
            if used + sym_tokens < budget:
                selected_symbols.append(sym)
                used += sym_tokens
            else:
                break

        sym_dicts = [_symbol_to_dict(s) for s in selected_symbols]

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
        call_graph_entries = self._extract_call_graph(path, graph)
        heritage_entries = self._extract_heritage(path, graph)
        community_label, community_cohesion = self._extract_community_meta(path, graph)

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

        # Community info: pick the dominant community label across files
        community_label = ""
        community_cohesion = 0.0
        labels = [fc.community_label for fc in file_contexts if fc.community_label]
        if labels:
            from collections import Counter
            community_label = Counter(labels).most_common(1)[0][0]
        cohesions = [fc.community_cohesion for fc in file_contexts if fc.community_cohesion > 0]
        if cohesions:
            community_cohesion = sum(cohesions) / len(cohesions)

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
                    communities_list.append({
                        "id": ci.id,
                        "label": ci.label,
                        "size": ci.size,
                        "cohesion": round(ci.cohesion, 2),
                    })
                communities_list.sort(key=lambda c: c["size"], reverse=True)
                communities_list = communities_list[:10]
            except Exception:
                pass

            try:
                flow_report = graph_builder.execution_flows()
                if flow_report and hasattr(flow_report, "flows"):
                    for flow in flow_report.flows[:5]:
                        execution_flows_list.append({
                            "entry_point": flow.entry_point,
                            "score": round(flow.score, 3),
                            "trace_length": len(flow.trace) if hasattr(flow, "trace") else 0,
                        })
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
    # Diff summary
    # ------------------------------------------------------------------

    def assemble_diff_summary(
        self,
        file_diffs: list[Any],  # list[FileDiff]
        affected_pages: Any,  # AffectedPages
        from_ref: str,
        to_ref: str,
    ) -> DiffSummaryContext:
        """Assemble context for the diff_summary template."""
        added_files = [d.path for d in file_diffs if d.status == "added"]
        deleted_files = [d.path for d in file_diffs if d.status == "deleted"]
        modified_files = [d.path for d in file_diffs if d.status == "modified"]

        symbol_diffs = [d.symbol_diff for d in file_diffs if d.symbol_diff is not None]

        affected_page_ids = list(affected_pages.regenerate) if affected_pages else []

        # Extract trigger commit from first diff that has it
        trigger_sha = None
        trigger_msg = None
        trigger_author = None
        diff_text = None
        for d in file_diffs:
            if getattr(d, "trigger_commit_sha", None):
                trigger_sha = d.trigger_commit_sha
                trigger_msg = d.trigger_commit_message
                trigger_author = d.trigger_commit_author
                diff_text = d.diff_text
                break

        return DiffSummaryContext(
            from_ref=from_ref,
            to_ref=to_ref,
            added_files=added_files,
            deleted_files=deleted_files,
            modified_files=modified_files,
            symbol_diffs=symbol_diffs,
            affected_page_ids=affected_page_ids,
            trigger_commit_sha=trigger_sha,
            trigger_commit_message=trigger_msg,
            trigger_commit_author=trigger_author,
            diff_text=diff_text,
        )

    # ------------------------------------------------------------------
    # Cross-package context (Phase 9 C2)
    # ------------------------------------------------------------------

    def assemble_cross_package(
        self,
        source_pkg: str,
        target_pkg: str,
        source_fcs: list[FilePageContext],
        target_fcs: list[FilePageContext],
        graph: Any,
    ) -> CrossPackageContext:
        """Assemble context for cross-package dependency page."""
        target_paths = {fc.file_path for fc in target_fcs}
        boundary = [fc for fc in source_fcs if any(d in target_paths for d in fc.dependencies)]
        used_syms = sorted(
            {
                sym["name"]
                for fc in target_fcs
                for sym in fc.symbols
                if sym.get("visibility") == "public"
            }
        )[:20]
        return CrossPackageContext(
            source_package=source_pkg,
            target_package=target_pkg,
            coupling_strength=len(boundary),
            used_symbols=used_syms,
            boundary_files=[fc.file_path for fc in boundary],
            source_pagerank_mean=sum(fc.pagerank_score for fc in source_fcs)
            / max(len(source_fcs), 1),
            target_pagerank_mean=sum(fc.pagerank_score for fc in target_fcs)
            / max(len(target_fcs), 1),
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
    # Graph intelligence extraction helpers
    # ------------------------------------------------------------------

    def _extract_call_graph(self, file_path: str, graph: Any) -> list[dict]:
        """Extract symbol-level call edges for symbols defined in this file."""
        entries: list[dict] = []
        try:
            for node, data in graph.nodes(data=True):
                if data.get("node_type") != "symbol":
                    continue
                if data.get("file_path") != file_path:
                    continue
                # Outgoing calls from this symbol
                for _, target, edata in graph.out_edges(node, data=True):
                    if edata.get("edge_type") == "calls":
                        tdata = graph.nodes.get(target, {})
                        entries.append({
                            "caller": data.get("name", node),
                            "callee": tdata.get("name", target),
                            "callee_file": tdata.get("file_path", ""),
                            "confidence": edata.get("confidence", 0.0),
                        })
                # Incoming calls to this symbol
                for source, _, edata in graph.in_edges(node, data=True):
                    if edata.get("edge_type") == "calls":
                        sdata = graph.nodes.get(source, {})
                        entries.append({
                            "caller": sdata.get("name", source),
                            "callee": data.get("name", node),
                            "callee_file": file_path,
                            "caller_file": sdata.get("file_path", ""),
                            "confidence": edata.get("confidence", 0.0),
                        })
        except Exception:
            pass
        # Deduplicate and cap
        seen: set[str] = set()
        unique: list[dict] = []
        for e in entries:
            key = f"{e.get('caller')}→{e.get('callee')}"
            if key not in seen:
                seen.add(key)
                unique.append(e)
        return unique[:15]

    def _extract_heritage(self, file_path: str, graph: Any) -> list[dict]:
        """Extract extends/implements edges for symbols in this file."""
        entries: list[dict] = []
        try:
            for node, data in graph.nodes(data=True):
                if data.get("node_type") != "symbol":
                    continue
                if data.get("file_path") != file_path:
                    continue
                for _, target, edata in graph.out_edges(node, data=True):
                    etype = edata.get("edge_type", "")
                    if etype in ("extends", "implements"):
                        tdata = graph.nodes.get(target, {})
                        entries.append({
                            "child": data.get("name", node),
                            "parent": tdata.get("name", target),
                            "kind": etype,
                            "parent_file": tdata.get("file_path", ""),
                        })
                for source, _, edata in graph.in_edges(node, data=True):
                    etype = edata.get("edge_type", "")
                    if etype in ("extends", "implements"):
                        sdata = graph.nodes.get(source, {})
                        entries.append({
                            "child": sdata.get("name", source),
                            "parent": data.get("name", node),
                            "kind": etype,
                            "child_file": sdata.get("file_path", ""),
                        })
        except Exception:
            pass
        return entries[:10]

    def _extract_community_meta(self, file_path: str, graph: Any) -> tuple[str, float]:
        """Extract community label and cohesion for a file node."""
        try:
            node_data = graph.nodes.get(file_path, {})
            meta = node_data.get("community_meta_json")
            if meta:
                import json as _json
                if isinstance(meta, str):
                    meta = _json.loads(meta)
                return meta.get("label", ""), meta.get("cohesion", 0.0)
        except Exception:
            pass
        return "", 0.0

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
