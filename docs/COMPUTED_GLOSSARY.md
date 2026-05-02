# Repowise Computed Glossary

This glossary describes the data Repowise computes while indexing, analyzing,
generating, serving, and exporting a repository. It is based on the code paths in
`packages/core`, `packages/server`, and `packages/cli`, not only on README files.

Use this as the vocabulary map for wiki pages, graph records, risk signals,
workspace overlays, MCP responses, and CLI output.

## Quick Map

| Area | Main code paths | What gets computed |
| --- | --- | --- |
| Traversal and parsing | `packages/core/src/repowise/core/ingestion/traverser.py`, `packages/core/src/repowise/core/ingestion/parser.py`, `packages/core/src/repowise/core/ingestion/models.py` | Files, languages, entry points, symbols, imports, exports, calls, inheritance, parse errors, content hashes |
| Graph construction | `packages/core/src/repowise/core/ingestion/graph.py`, `call_resolver.py`, `heritage_resolver.py`, `framework_edges.py`, `dynamic_hints/` | File and symbol nodes, import/call/heritage/framework/dynamic/co-change edges, centrality, SCCs, communities, execution flows |
| Git intelligence | `packages/core/src/repowise/core/ingestion/git_indexer.py` | Churn, ownership, hotspots, bus factor, co-change partners, significant commits, temporal scores, rename and merge signals |
| Analysis | `packages/core/src/repowise/core/analysis/` | Dead-code findings, decision records, decision staleness, security findings, PR blast radius, execution flows, communities |
| Generation | `packages/core/src/repowise/core/generation/` | Wiki page contexts, page types, source hashes, summaries, freshness, confidence decay, RAG context, job checkpoints, reports, costs |
| Workspace intelligence | `packages/core/src/repowise/core/workspace/` | Workspace repo scan, cross-repo co-changes, package dependencies, API contracts, contract links, workspace CLAUDE.md data |
| Persistence and search | `packages/core/src/repowise/core/persistence/`, Alembic migrations | ORM rows, FTS rows, vector records, answer cache, cost rows, graph rows |
| API, MCP, CLI | `packages/server/src/repowise/server/`, `packages/cli/src/repowise/cli/` | Dashboard schemas, MCP tool payloads, status tables, doctor checks, exports, costs, augment hook context |

## Traversal And Repository Structure

| Term | Definition | Computed by | Example |
| --- | --- | --- | --- |
| Includable source file | A file that survives ignore rules, blocked patterns, size limit, binary detection, generated-file detection, and language detection. | `FileTraverser._build_file_info()` | `packages/core/src/repowise/core/ingestion/parser.py` |
| `FileInfo` | Per-file metadata used by the parser and graph builder. | `FileTraverser.traverse()` | `{path: "src/app.py", language: "python", is_test: false, is_entry_point: true}` |
| Language tag | Canonical language value from file extension, special filename, or shebang. | `ingestion/models.py`, `traverser.py`, `languages/registry.py` | `python`, `typescript`, `go`, `terraform`, `openapi`, `unknown` |
| Test file flag | Whether a file looks like a test/spec/fixture file. | `FileTraverser._build_file_info()` and community/test-gap helpers | `tests/test_auth.py -> is_test=true` |
| Config file flag | Whether a file is classified as configuration. | `FileTraverser._build_file_info()` | `pyproject.toml -> is_config=true` |
| API contract flag | Whether a file is an API contract format. | `FileTraverser._build_file_info()` | `openapi.yaml -> is_api_contract=true` |
| Entry point flag | Whether a filename or language-specific entry pattern marks a file as a starting point. | `FileTraverser._build_file_info()` | `main.py`, `server.ts`, `Dockerfile` depending on rules |
| Traversal stats | Counts of included files and skip reasons. | `TraversalStats` in `traverser.py` | `{included: 240, skipped_binary: 3, skipped_generated: 12}` |
| Package info | A package/workspace detected from manifests near the repo root. | `FileTraverser._detect_monorepo()` | `{name: "core", path: "packages/core", manifest_file: "pyproject.toml"}` |
| Repo structure | High-level structure summary used by overview generation. | `FileTraverser.get_repo_structure()` | `{is_monorepo: true, total_files: 820, entry_points: ["packages/cli/src/.../main.py"]}` |
| Language distribution | Fraction of included files by language. | `get_repo_structure()` | `{"python": 0.72, "typescript": 0.18, "markdown": 0.10}` |
| Estimated LOC | Fast line-count estimate from file sizes, not exact source line counting. | `get_repo_structure()` | `total_loc = sum(size_bytes // 40)` |
| Content hash | SHA-256 of raw file bytes. | `compute_content_hash()` in `ingestion/models.py` | `3f786850e387550fdab836ed7e6dc881de23001b...` |

## Parsing, Symbols, Imports, Calls

| Term | Definition | Computed by | Example |
| --- | --- | --- | --- |
| `ParsedFile` | Full parse result for one file: file metadata, symbols, imports, exports, calls, heritage, docstring, parse errors, content hash. | `ASTParser.parse_file()` | `ParsedFile(symbols=[...], imports=[...], calls=[...])` |
| Symbol | A function, class, method, interface, enum, constant, type alias, module, macro, variable, etc. | `ASTParser._extract_symbols()` | `src/app.py::create_app` |
| Symbol ID | Stable ID derived from path and name, including parent class for methods. | `ASTParser._extract_symbols()` | `src/models.py::User::save` |
| Qualified name | Dot-form symbol name derived from path and parent. | `_build_qualified_name()` | `src.models.User.save` |
| Symbol kind | Canonical symbol type. | `LanguageConfig.symbol_node_types` plus refiners | `function`, `class`, `method`, `interface`, `struct`, `trait` |
| Signature | Compact declaration text. | `build_signature()` via parser extractors | `def create_app(config: Config) -> FastAPI` |
| Symbol docstring | Human text attached to a symbol, when extractable. | `extract_symbol_docstring()` | `"Create and configure the API app."` |
| Module docstring | File-level docstring. | `extract_module_docstring()` | `"Command-line entry points."` |
| Visibility | Public/private/protected/internal classification. | Language-specific visibility helpers | `_helper -> private`, `UserService -> public` |
| Async flag | Whether a symbol is async. | `_is_async_node()` | `async def fetch() -> is_async=true` |
| Complexity estimate | Symbol complexity field, persisted to symbols. | Parser/model pipeline; defaults to `1` unless language extraction enriches it | `complexity_estimate: 3` |
| Decorators | Decorator/modifier strings captured with a symbol. | `ASTParser._extract_symbols()` | `["@router.get('/users')"]` |
| Import | Raw import statement plus normalized module path and imported names. | `ASTParser._extract_imports()` | `{raw_statement: "from .db import Session", module_path: ".db", imported_names: ["Session"]}` |
| Named binding | Alias-aware import binding. | `extract_import_bindings()` | `{local_name: "np", exported_name: null, is_module_alias: true}` |
| Resolved import | Import whose module path was matched to a repo file. | `GraphBuilder.build()` through `resolve_import()` | `from .models import User -> src/models.py` |
| Export list | Public top-level symbol names exported by a file. | `ASTParser._derive_exports()` | `["create_app", "Settings"]` |
| Call site | Raw function or method call extracted from the AST. | `ASTParser._extract_calls()` | `{target_name: "save", receiver_name: "user", line: 42, argument_count: 1}` |
| Enclosing caller symbol | The symbol that contains a call site. | `_find_enclosing_symbol()` | `src/app.py::main` |
| Heritage relation | Raw inheritance or implementation relationship. | `extract_heritage()` | `OrderController extends BaseController` |
| Parse error | Non-fatal syntax/tree-sitter error description. | `_collect_error_nodes()` | `Parse error at line 17` |

## Graph Entities And Edges

| Term | Definition | Computed by | Example |
| --- | --- | --- | --- |
| Dependency graph | Directed NetworkX graph containing file nodes, symbol nodes, and edge metadata. | `GraphBuilder` | `nx.DiGraph` with nodes `src/app.py`, `src/app.py::main` |
| File node | Graph node for a source file. | `GraphBuilder.add_file()` | `{node_type: "file", language: "python", symbol_count: 8}` |
| Symbol node | Graph node for an extracted symbol. | `GraphBuilder.add_file()` | `{node_type: "symbol", kind: "function", name: "main"}` |
| External node | Node for third-party or unresolvable dependencies. | Import resolution paths | `external:react` |
| Synthetic module symbol | Symbol node for top-level calls in a file. | `GraphBuilder.add_file()` | `src/app.py::__module__` |
| `defines` edge | File-to-symbol containment. | `GraphBuilder.add_file()` | `src/app.py -> src/app.py::main` |
| `imports` edge | File-to-file import relationship. | `GraphBuilder.build()` | `src/app.py -> src/settings.py` |
| `imported_names` edge payload | Names imported along an import edge. | `GraphBuilder.build()` | `["Settings", "load_config"]` |
| `has_method` edge | Class-to-method containment. | `GraphBuilder.add_file()` | `src/models.py::User -> src/models.py::User::save` |
| `calls` edge | Symbol-to-symbol call relationship. | `CallResolver`, then `GraphBuilder._resolve_calls()` | `src/app.py::main -> src/db.py::connect` |
| Call confidence | Confidence that a call edge points to the right callee. | `CallResolver` | `0.95` same-file, `0.90` import binding, `0.50` global unique |
| `extends` edge | Class/struct inheritance edge. | `HeritageResolver` | `UserView -> BaseView` |
| `implements` edge | Interface/trait implementation edge. | `HeritageResolver` | `UserRepository -> Repository` |
| Heritage confidence | Confidence that inheritance/implementation resolved correctly. | `HeritageResolver` | `0.95` same-file, `0.90` imported, `0.50` global unique |
| `framework` edge | Synthetic edge from framework conventions. | `framework_edges.py` | `urls.py -> views.py`, `app.py -> routers/users.py` |
| Dynamic edge | Edge inferred from runtime/dynamic patterns. | `dynamic_hints/*` and `GraphBuilder.add_dynamic_edges()` | `{edge_type: "dynamic_imports", hint_source: "django", weight: 1.0}` |
| `co_changes` edge | File-to-file historical coupling edge. | `GraphBuilder.add_co_change_edges()` from git metadata | `src/a.py -> src/b.py` with `weight: 4.2` |
| Stem map | Import-stem to candidate file path lookup used for import resolution. | `GraphBuilder._build_stem_map()` | `{"models": ["src/models.py", "tests/models.py"]}` |
| File subgraph | File-only graph used for PageRank and betweenness. | `GraphBuilder.file_subgraph()` | All file/external nodes, excluding `co_changes` edges |
| PageRank | File centrality in the import graph. | `GraphBuilder.pagerank()` | `0.01842` |
| Betweenness | How often a file sits on shortest paths. | `GraphBuilder.betweenness_centrality()` | `0.0067` |
| SCC | Strongly connected component, used to detect dependency cycles. | `GraphBuilder.strongly_connected_components()` | `{"src/a.py", "src/b.py"}` |
| SCC page group | Non-singleton SCC that gets a cycle page. | `PageGenerator.generate_all()` | `scc-3` |
| Graph JSON | Node-link serialization of the graph. | `GraphBuilder.to_json()` | `{"directed": true, "nodes": [...], "links": [...]}` |

## Communities And Execution Flows

| Term | Definition | Computed by | Example |
| --- | --- | --- | --- |
| File community | Cluster of related production files, with tests assigned to their most-related production community. | `detect_file_communities()` | `community_id: 2` |
| Symbol community | Cluster of symbol nodes based on call and heritage edges. | `detect_symbol_communities()` | `symbol_community_id: 5` |
| Community algorithm | Partition algorithm used. | `communities._partition()` | `leiden`, `louvain`, `none`, `failed` |
| Oversized community split | Second partition pass for communities larger than a graph fraction. | `_split_oversized()` | A 300-file cluster split into smaller clusters |
| Community label | Human label derived from non-generic path segments or filename keywords. | `_heuristic_label()` | `api/routes`, `auth`, `payments` |
| Community cohesion | Ratio of actual intra-community edges to possible edges. | `_cohesion_score()` | `0.2143` |
| Dominant language | Most common language among community members. | `_dominant_language()` | `python` |
| Neighboring community | Adjacent community from graph edges, surfaced by MCP/API. | `tool_community.py`, graph routers | `{community_id: 4, edge_count: 9}` |
| Entry point score | 0 to 1 score for a function/method as an execution start. | `_score_entry_point()` | `0.735` for `main()` |
| Entry point score signals | Weighted fan-out, low in-degree, visibility, name pattern, and file entry flag. | `_score_entry_point()` | public `main()` with many calls scores high |
| Execution flow | BFS trace following high-confidence call edges from an entry point. | `trace_execution_flows()` | `main -> load_config -> connect_db` |
| Cross-community flow | Execution flow that visits more than one community. | `_bfs_trace()` | `communities_visited: [0, 3]` |
| Flow depth | Number of call hops in a traced flow. | `_bfs_trace()` | `depth: 4` |
| Flow deduplication | Keeps the longest flow per shared first-three-node prefix. | `_deduplicate_flows()` | Two `main -> route -> handler` traces collapse to one |

## Git Intelligence

| Term | Definition | Computed by | Example |
| --- | --- | --- | --- |
| Git metadata row | Per-file history, ownership, churn, and coupling record. | `GitIndexer.index_repo()` and `_index_file()` | One `git_metadata` row for `src/app.py` |
| Commit counts | Total, 90-day, and 30-day commit volumes. | `_index_file()` | `{commit_count_total: 87, commit_count_90d: 12, commit_count_30d: 3}` |
| Commit count capped | Whether the history reached the configured commit limit. | `_index_file()` | `true` when `len(commits) >= 500` |
| First/last commit timestamps | Oldest and newest commit timestamps for a file. | `_index_file()` | `first_commit_at: 2024-05-03T10:00:00Z` |
| File age days | Days since first commit. | `_index_file()` | `age_days: 455` |
| Primary owner | Dominant owner by blame when available, otherwise by commit count. | `_get_blame_ownership()` and `_index_file()` | `{name: "Asha", email: "asha@example.com", pct: 0.64}` |
| Top authors | Top five authors by commit count. | `_index_file()` | `[{name: "Asha", commit_count: 20}]` |
| Recent owner | Dominant committer in the last 90 days. | `_index_file()` | `recent_owner_name: "Sam"` |
| Contributor count | Number of distinct authors. | `_index_file()` | `contributor_count: 6` |
| Bus factor | Number of contributors needed to account for 80 percent of commits. | `_index_file()` | `bus_factor: 2` |
| Significant commits | Filtered, non-noise commit messages useful for decisions and risk. | `_is_significant_commit()` | `[{sha: "a1b2c3d4", message: "migrate auth to JWT"}]` |
| PR number | PR/MR number extracted from significant commit messages. | `_PR_NUMBER_RE` in `git_indexer.py` | `pr_number: 128` |
| Commit categories | Message classification counts. | `_COMMIT_CATEGORIES` in `git_indexer.py` | `{"feature": 4, "fix": 11, "refactor": 2}` |
| Lines added/deleted 90d | Recent churn by numstat. | `_index_file()` | `{lines_added_90d: 340, lines_deleted_90d: 87}` |
| Average commit size | `(lines_added_90d + lines_deleted_90d) / commit_count_90d`. | `_index_file()` | `35.6` |
| Merge commit count 90d | Number of merge commits touching the file recently. | `_index_file()` | `merge_commit_count_90d: 2` |
| Original path | Earliest path found through rename-follow history. | `_detect_original_path()` | `legacy/auth/session.py` |
| Temporal hotspot score | Exponentially decayed churn score with 180-day half-life. | `_index_file()` | `2.43` |
| Churn percentile | Rank percentile among indexed files by temporal hotspot score, with 90-day commits as tiebreak. | `_compute_percentiles()` | `0.88` |
| Hotspot flag | Top churn file: percentile >= 0.75 and has recent commits. | `_compute_percentiles()` | `is_hotspot: true` |
| Stable file flag | File with more than 10 total commits and no recent 90-day commits. | `_index_file()` | `is_stable: true` |
| Co-change partner | File historically changed in the same commits, with temporal decay. | `_compute_co_changes()` | `{file_path: "src/schema.py", co_change_count: 3.72, last_co_change: "2026-04-14"}` |
| Git index summary | Repo-level indexing result. | `GitIndexSummary` | `{files_indexed: 420, hotspots: 38, stable_files: 71, duration_seconds: 12.4}` |

## Generated Wiki Pages

| Term | Definition | Computed by | Example |
| --- | --- | --- | --- |
| Page type | Kind of generated documentation page. | `PageType` in `generation/models.py` | `file_page`, `module_page`, `repo_overview` |
| Generation level | Ordered generation tier for page dependencies. | `GENERATION_LEVELS` | `api_contract: 0`, `file_page: 2`, `repo_overview: 6` |
| Generated page | Markdown wiki page plus metadata and token counts. | `GeneratedPage` and `PageGenerator._build_generated_page()` | `{page_id: "file_page:src/app.py", title: "File: src/app.py"}` |
| Page ID | Deterministic natural key. | `compute_page_id()` | `symbol_spotlight:src/app.py::create_app` |
| Source hash | SHA-256 of rendered prompt/source context for freshness comparisons. | `compute_source_hash()` | 64-character hex |
| Page summary | Deterministic first prose paragraph or overview excerpt. | `PageGenerator._extract_summary()` | `"This file wires the CLI command group and registers subcommands."` |
| Freshness status | Whether a page still matches current source and age thresholds. | `compute_freshness()` | `fresh`, `stale`, `expired` |
| Confidence decay | Linear decay from 1.0 to 0.0 over expiry days. | `decay_confidence()` | `0.77` after part of the expiry window |
| Git-adjusted confidence decay | Multiplier adjusted by hotspot/stable state and commit message intent. | `compute_confidence_decay_with_git()` | Direct refactor on hotspot decays faster |
| Prompt cache key | SHA-256 of model, language, page type, and prompt. | `PageGenerator._compute_cache_key()` | `9e107d9d372bb6826bd81d3542a419d6...` |
| Cached tokens | Tokens served from provider cache. | Provider response, persisted on pages and report | `cached_tokens: 12000` |
| Hallucination warning | LLM output mentions symbol-like backticks not found in parsed symbols. | `_validate_symbol_references()` | `Unknown symbol: "run_worker"` |
| Generation report | Run summary by page type, tokens, stale pages, dead-code count, decision count, warnings, elapsed time. | `GenerationReport.from_pages()` | `{pages_by_type: {"file_page": 45}, total_input_tokens: 980000}` |
| Estimated generation cost | Token estimate using USD per 1M-token rates. | `GenerationReport.estimated_cost_usd()` and CLI `cost_estimator.py` | `$2.3400` |
| Generation job checkpoint | JSON state for resumable generation. | `JobSystem` | `{status: "running", completed_pages: 12, current_level: 2}` |
| Generation status | Job lifecycle state. | `JobSystem` and `GenerationJob` ORM | `pending`, `running`, `completed`, `failed`, `paused` |

## Page Contexts

| Term | Definition | Computed by | Example |
| --- | --- | --- | --- |
| File page context | Template data for one important source file. | `ContextAssembler.assemble_file_page()` | `{file_path, symbols, imports, dependencies, pagerank_score}` |
| Symbol spotlight context | Template data for a top public symbol. | `assemble_symbol_spotlight()` | `create_app` with signature, source body, callers |
| Module page context | Aggregate context for top-level directory/module. | `assemble_module_page()` | `{module_path: "packages/core", total_symbols: 780}` |
| SCC page context | Context for a circular dependency cycle. | `assemble_scc_page()` | `cycle_description: "Circular dependency cycle: a.py -> b.py"` |
| Repo overview context | Whole-repo summary context. | `assemble_repo_overview()` | `language_distribution`, `top_files_by_pagerank`, `circular_dependency_count` |
| Architecture diagram context | Top PageRank nodes, selected edges, communities, SCC groups. | `assemble_architecture_diagram()` | Mermaid graph inputs for 50 nodes and 200 edges |
| API contract context | Raw API contract plus endpoint/schema hints. | `assemble_api_contract()` | `endpoints: ["GET /users"]`, `schemas: ["User"]` |
| Infra page context | Raw infra file plus target names. | `assemble_infra_page()` | `Dockerfile`, `Makefile`, `terraform` files |
| Diff summary context | Changed files, symbol diffs, affected pages, trigger commit/diff. | `assemble_diff_summary()` | `{added_files: ["src/new.py"], affected_page_ids: [...]}` |
| Cross-package context | Monorepo boundary summary between packages. | `assemble_cross_package()` | `{source_package: "cli", target_package: "core", coupling_strength: 5}` |
| Dependency summaries | Summaries of already-generated dependency pages. | `assemble_file_page()` with `page_summaries` | `{ "src/db.py": "Database access layer..." }` |
| RAG context | Snippets from vector search for related generated pages. | `_generate_file_page_from_ctx()` | `["[file_page:src/schema.py]\nDefines API schema..."]` |
| Token estimate | `len(text) // 4` heuristic. | `ContextAssembler._estimate_tokens()` | `3200` |
| Structural summary mode | Large-file outline instead of raw source snippet. | `_build_structural_summary()` | `[Large file - structural summary mode]` |
| Significant file | File selected for its own `file_page`. | `_is_significant_file()` | Entry point, top PageRank, bridge file, package `__init__.py`, or test with symbols |
| Top symbol selection | Public symbols selected by their file PageRank and percentile budget. | `PageGenerator.generate_all()` | Top 10 percent of public symbols, capped by page budget |
| Page budget | Hard cap `max(50, int(num_files * max_pages_pct))`. | `PageGenerator.generate_all()` | 800 files with 10 percent cap -> 80-page budget |

## Dead Code

| Term | Definition | Computed by | Example |
| --- | --- | --- | --- |
| Dead-code finding | A graph/git finding persisted to `dead_code_findings`. | `DeadCodeAnalyzer` | `{kind: "unused_export", file_path: "src/api.py", confidence: 0.7}` |
| Unreachable file | File with no incoming imports, not an entry point/test/config/contract/whitelisted file. | `_detect_unreachable_files()` | `src/legacy_adapter.py` |
| Unused export | Public symbol in an imported file that no importer names. | `_detect_unused_exports()` | `symbol_name: "OldClient"` |
| Unused internal | Private/internal symbol with no incoming `calls` edges. | `_detect_unused_internals()` | `_parse_legacy_token` |
| Zombie package | Monorepo top-level package with no external package importers. | `_detect_zombie_packages()` | `packages/old-sdk` |
| Dead-code confidence | Heuristic certainty based on age, recent commits, importers, dynamic imports, and deprecation hints. | `DeadCodeAnalyzer` | `1.0` for year-old unreachable file |
| Safe-to-delete flag | Whether confidence passes delete threshold and dynamic patterns do not block deletion. | `_make_unreachable_finding()` and other passes | `safe_to_delete: true` |
| Dead-code evidence | Human-readable reasons for the finding. | `DeadCodeAnalyzer` | `["in_degree=0 (no files import this)", "No commits in last 90 days"]` |
| Estimated deletable lines | Sum of line estimates for safe findings. | `DeadCodeAnalyzer.analyze()` | `deletable_lines: 420` |
| Confidence summary | Counts of high, medium, low confidence findings. | `DeadCodeAnalyzer.analyze()` | `{"high": 12, "medium": 8, "low": 0}` |
| Finding status | User triage status persisted in DB. | `DeadCodeFinding.status` | `open`, `acknowledged`, `resolved`, `false_positive` |

## Decisions And Governance

| Term | Definition | Computed by | Example |
| --- | --- | --- | --- |
| Decision record | ADR-like row from code comments, git, docs, or CLI/manual entry. | `DecisionExtractor`, CRUD, CLI | `{title: "Use Redis for sessions", status: "active"}` |
| Inline marker decision | Decision extracted from comments such as `WHY:`, `DECISION:`, `TRADEOFF:`, `ADR:`. | `scan_inline_markers()` | `# DECISION: cache auth sessions in Redis` |
| Git archaeology decision | LLM-structured decision inferred from significant commit messages with decision keywords. | `mine_git_archaeology()` | `migrate from REST client to generated OpenAPI client` |
| README-mined decision | Decision extracted from docs such as README, CLAUDE, ARCHITECTURE, DESIGN. | `mine_readme_docs()` | `"We use SQLite by default because setup should be local-first."` |
| Decision source | Provenance of a record. | `DecisionRecord.source` | `inline_marker`, `git_archaeology`, `readme_mining`, `cli` |
| Decision confidence | Source-specific extraction confidence. | `DecisionExtractor` | `0.95` inline LLM, `0.70` git signal, `0.60` README mining, `1.0` manual |
| Affected files | Files linked to a decision from graph neighbors, commit files, or manual input. | `DecisionExtractor` | `["src/auth.py", "src/session.py"]` |
| Affected modules | Top-level modules inferred from affected files or text. | `_infer_modules()` | `["src", "packages"]` |
| Decision tags | Topic labels inferred from keywords or LLM output. | `_infer_tags()` and prompts | `auth`, `database`, `api`, `security`, `testing` |
| Decision status | Lifecycle state. | `DecisionRecord.status` | `proposed`, `active`, `deprecated`, `superseded` |
| Decision staleness score | 0 to 1 score indicating code has moved since a decision. | `DecisionExtractor.compute_staleness()` and `crud.recompute_decision_staleness()` | `0.63` |
| Conflict boost | Staleness increase when newer commit messages contain contradiction signals and overlap decision text. | `compute_staleness()` | `+0.3` for "migrate away" touching the same concept |
| Decision health summary | Counts and lists for stale, proposed, and ungoverned hotspots. | `get_decision_health_summary()` and server/CLI routes | `{active: 10, stale: 2, proposed: 3}` |
| Ungoverned hotspot | Hot file without related architectural decision coverage. | Decision health computation | `src/payments/processor.py` |

## Security Findings

| Term | Definition | Computed by | Example |
| --- | --- | --- | --- |
| Security finding | Regex or symbol-name signal persisted to `security_findings`. | `SecurityScanner.scan_file()` | `{kind: "hardcoded_secret", severity: "high", line: 12}` |
| High severity finding | Dangerous execution, deserialization, shell, or hardcoded secret/password pattern. | `_PATTERNS` in `security_scan.py` | `eval_call`, `pickle_loads`, `hardcoded_password` |
| Medium severity finding | SQL construction or TLS verification issue. | `_PATTERNS` | `fstring_sql`, `concat_sql`, `tls_verify_false` |
| Low severity finding | Weak hash or security-sensitive symbol name. | `_PATTERNS` and symbol scan | `weak_hash`, `security_sensitive_symbol` |
| Security snippet | Trimmed source line or symbol name for context. | `SecurityScanner.scan_file()` | `password = "admin"` |

## Risk And Blast Radius

| Term | Definition | Computed by | Example |
| --- | --- | --- | --- |
| File risk score | Pagerank centrality multiplied by `1 + temporal_hotspot_score`. | `PRBlastRadiusAnalyzer._score_file()` | `0.018 * (1 + 2.4) = 0.0612` |
| Overall PR risk score | 0 to 10 composite using average direct risk, max direct risk, and transitive breadth. | `_compute_overall_risk()` | `7.25` |
| Transitive affected file | Importer reached by reverse BFS from changed files. | `_transitive_affected()` | `{path: "src/api.py", depth: 2}` |
| Co-change warning | Historical co-change partner missing from a PR/change set. | `_cochange_warnings()` | `{changed: "src/a.py", missing_partner: "src/b.py", score: 4.2}` |
| Recommended reviewer | Owner aggregate over changed and affected files. | `_recommend_reviewers()` | `{email: "asha@example.com", files: 7, ownership_pct: 0.63}` |
| Test gap | File lacking a matching test path by basename conventions. | `_find_test_gaps()` and MCP `_check_test_gap()` | `src/auth.py -> true` |
| Risk trend | Velocity from 30-day vs prior 60-day commit rates. | `tool_risk._compute_trend()` | `increasing`, `stable`, `decreasing` |
| Risk type | Human bucket for the kind of risk. | `tool_risk._classify_risk_type()` | `bug-prone`, `churn-heavy`, `bus-factor-risk`, `high-coupling`, `stable` |
| Change pattern | Human label from dominant commit category. | `tool_risk._derive_change_pattern()` | `feature-active`, `fix-heavy`, `dependency-churn`, `mixed-activity` |
| Impact surface | Top critical reverse dependencies within two hops. | `tool_risk._compute_impact_surface()` | `[{file_path: "src/api.py", pagerank: 0.05}]` |
| Risk summary | One-line synthesized risk sentence for MCP. | `tool_risk._assess_one_target()` | `src/auth.py - hotspot score 88% (increasing), 6 dependents...` |
| Top hotspots | Highest churn/hotspot files returned for context. | `get_risk()` | `[{file_path: "src/db.py", hotspot_score: 0.94}]` |

## Search, Answer Cache, And Retrieval

| Term | Definition | Computed by | Example |
| --- | --- | --- | --- |
| Search result | Unified full-text or vector result. | `SearchResult` in `persistence/search.py` | `{page_id, title, page_type, target_path, score, snippet, search_type}` |
| FTS5 query | Stop-word-stripped OR prefix query for SQLite. | `_build_fts5_query()` | `"auth"* OR "session"*` |
| FTS score | Positive score from negated SQLite rank or Postgres `ts_rank`. | `FullTextSearch` | `0.734` |
| Vector score | Cosine similarity between query embedding and page embedding. | `InMemoryVectorStore.search()` and other vector stores | `0.812` |
| Snippet | First 200 chars of indexed content. | `_snippet()` or vector metadata | `"This module handles..."` |
| Answer cache row | Cached MCP answer payload. | `tool_answer.py` and `AnswerCache` ORM | `{question_hash, payload_json, provider_name, model_name}` |
| Question hash | SHA-256 of normalized question text. | `tool_answer._hash_question()` | Same hash for `"How auth works?"` with extra whitespace/case |
| Answer payload | Cached `get_answer` result. | `get_answer()` | `{answer, citations, confidence, fallback_targets, retrieval}` |
| Retrieval hit | Search hit hydrated with page metadata and summary. | `tool_answer.py` retrieval pipeline | `{target_path: "src/auth.py", score: 3.2, summary: "..."}` |
| Retrieval dominance | Gating logic comparing top and second search scores. | `tool_answer.py` | Top score high enough to answer from dominant hit |
| Federated RRF score | Reciprocal rank fusion score for workspace search across repos. | `tool_search.py` | `rrf_score: 0.0164` |
| Confidence score | Normalized workspace search confidence. | `tool_search.py` | `confidence_score: 0.87` |

## Persistence Tables And Stored Entities

| Table or store | Computed content | Example |
| --- | --- | --- |
| `repositories` | Repo identity plus current indexed `head_commit` and settings JSON. | `{name: "repowise", default_branch: "main"}` |
| `generation_jobs` | Long-running generation progress. | `{status: "running", total_pages: 120, completed_pages: 31}` |
| `wiki_pages` | Current generated markdown pages and freshness metadata. | `file_page:src/app.py` |
| `wiki_page_versions` | Archived historical snapshots on regeneration. | `version: 3` |
| `graph_nodes` | File and symbol nodes with graph metrics and community metadata. | `{node_id: "src/app.py", pagerank: 0.02}` |
| `graph_edges` | Typed relationships with imported names and confidence. | `{source: "src/app.py", target: "src/db.py", edge_type: "imports"}` |
| `wiki_symbols` | Parsed symbols projected into DB. | `{symbol_id: "src/app.py::main", kind: "function"}` |
| `git_metadata` | Per-file history, churn, ownership, hotspots, co-changes. | `{file_path: "src/app.py", is_hotspot: true}` |
| `decision_records` | Extracted/manual architectural decisions and staleness. | `{title: "Use Postgres for production", status: "active"}` |
| `dead_code_findings` | Dead-code analyzer findings and triage status. | `{kind: "unreachable_file", safe_to_delete: true}` |
| `security_findings` | Static security signals. | `{kind: "eval_call", severity: "high"}` |
| `llm_costs` | Per-call token and USD cost rows. | `{operation: "doc_generation", input_tokens: 2500, cost_usd: 0.012}` |
| `answer_cache` | Cached MCP answer payloads keyed by normalized question. | `{question: "How does auth work?", question_hash: "..."}` |
| `conversations` and `chat_messages` | Chat state and structured message JSON. | `{role: "assistant", content_json: {...}}` |
| `webhook_events` | Received external events and processing status. | `{provider: "github", event_type: "push", processed: false}` |
| SQLite `page_fts` | FTS5 mirror of page title/content. | Used by full-text search |
| Postgres `wiki_pages.embedding` | pgvector embedding column, conditionally added by migration. | 1536-dim vector |
| LanceDB `wiki_pages` table | Local vector index with page metadata. | `{page_id, vector, title, page_type, target_path}` |

## LLM Cost And Provider Usage

| Term | Definition | Computed by | Example |
| --- | --- | --- | --- |
| Pricing table | USD per million input/output tokens by model. | `generation/cost_tracker.py` | `claude-sonnet-4-6: {input: 3.0, output: 15.0}` |
| Fallback pricing | Default pricing for unknown models. | `_get_pricing()` | `{input: 3.0, output: 15.0}` |
| Call cost | `(input_tokens * input_rate + output_tokens * output_rate) / 1_000_000`. | `CostTracker.record()` | `1000 in, 500 out on Sonnet -> $0.0105` |
| Session cost | Cumulative USD for one tracker instance. | `CostTracker.session_cost` | `2.37` |
| Session tokens | Cumulative input plus output tokens. | `CostTracker.session_tokens` | `845000` |
| Cost totals | DB aggregate grouped by operation, model, or day. | `CostTracker.totals()` | `{group: "file_page", calls: 42, cost_usd: 1.12}` |
| CLI cost estimate | Pre-generation token/cost plan. | `packages/cli/src/repowise/cli/cost_estimator.py` | `{estimated_pages: 82, estimated_cost_usd: 4.60}` |

## Workspace Intelligence

| Term | Definition | Computed by | Example |
| --- | --- | --- | --- |
| Discovered repo | Candidate git repo found under a workspace root. | `workspace/scanner.py` | `{alias: "api", path: "services/api"}` |
| Workspace config | Parsed `.repowise-workspace.yaml`. | `workspace/config.py` | `{repos: [{alias: "web", path: "apps/web"}]}` |
| Repo update result | Per-repo update outcome for workspace update/watch. | `workspace/update.py` | `{alias: "core", updated: true, file_count: 420, symbol_count: 2100}` |
| Cross-repo co-change | File pair in different repos changed by same author within a time window, weighted by recency. | `detect_cross_repo_co_changes()` | `{source_repo: "api", source_file: "routes/users.py", target_repo: "web", target_file: "users.tsx", strength: 1.34}` |
| Cross-repo package dependency | Manifest path dependency from one repo to another. | `detect_package_dependencies()` | `{source_repo: "web", target_repo: "shared", kind: "npm_workspace"}` |
| Cross-repo overlay | JSON payload saved under workspace data dir. | `run_cross_repo_analysis()` | `{co_changes: [...], package_deps: [...], repo_summaries: {...}}` |
| Cross-repo edge count | Per-repo count of co-change and package-dependency edges. | `_build_repo_summaries()` | `{cross_repo_edge_count: 12}` |
| Workspace CLAUDE.md data | Per-repo summaries plus cross-repo overlays and contract links. | `generation/editor_files/data.py`, `claude_md.py` | `{repos: [...], co_changes: [...], contract_links: [...]}` |

## API Contracts

| Term | Definition | Computed by | Example |
| --- | --- | --- | --- |
| Contract | Provider or consumer API endpoint/topic/service extracted from source. | `workspace/contracts.py` and extractors | `{contract_id: "http::GET::/api/users/{param}", role: "provider"}` |
| Contract type | API surface kind. | Contract extractors | `http`, `grpc`, `topic` |
| Contract role | Whether source provides or consumes the contract. | Extractors | `provider`, `consumer` |
| Contract confidence | Extraction strategy confidence. | Extractors and contract matching | `0.8` |
| Service boundary | Monorepo service path assigned to contracts. | `workspace/extractors/service_boundary.py` | `services/billing` |
| Normalized contract ID | Lowercase/canonical ID used for matching. | `normalize_contract_id()` | `http::GET::/Api/Users/ -> http::GET::/api/users` |
| Contract link | Matched provider-consumer pair across repos/services. | `match_contracts()` | `{provider_repo: "api", consumer_repo: "web", match_type: "exact"}` |
| Manual contract link | Workspace-configured provider/consumer link. | `_build_manual_links()` | `{match_type: "manual", confidence: 1.0}` |
| Contract store | JSON payload saved as `contracts.json`. | `run_contract_extraction()` | `{contracts: [...], contract_links: [...]}` |

## Knowledge Map

| Term | Definition | Computed by | Example |
| --- | --- | --- | --- |
| Top owner | Owner ranked by number of files primarily owned. | `server/services/knowledge_map.py` | `{email: "asha@example.com", files_owned: 42, percentage: 18.6}` |
| Knowledge silo | File where one owner has more than 80 percent ownership. | `compute_knowledge_map()` | `{file_path: "src/auth.py", owner_pct: 0.91}` |
| Onboarding target | High-PageRank file with few or no documentation words. | `compute_knowledge_map()` | `{path: "src/core.py", pagerank: 0.04, doc_words: 0}` |
| Documentation word count | Word count of the generated file page content. | `compute_knowledge_map()` | `doc_words: 640` |

## CLI-Visible Computed Outputs

| Command | Computed output | Example |
| --- | --- | --- |
| `repowise status` | Sync state, current HEAD, indexed commit, DB page counts, graph node counts, pages by type, token totals. | `file_page: 52`, `Status: 3 new commit(s)` |
| `repowise status --workspace` | Per-repo file/symbol counts, indexed age, HEAD short SHA, stale/up-to-date state. | `api 420 files 2,100 symbols 2h ago a1b2c3d stale` |
| `repowise doctor` | Health checks for DB, pages, vector store, FTS, graph, stale pages, store drift, coordinator state. | `SQL <-> Vector Store: 3 missing` |
| `repowise search` | Full-text/vector/wiki or symbol hits. | `score 0.83, file_page, src/auth.py` |
| `repowise dead-code` | Dead-code table or JSON report. | `unused_export src/api.py OldClient 0.70` |
| `repowise decision` | Decision list, detail view, health summary, stale records, proposed records, ungoverned hotspots. | `Stale decisions: 2` |
| `repowise costs` | Grouped LLM cost totals. | `group=file_page, calls=45, cost=$1.37` |
| `repowise export` | Markdown/HTML/JSON export entries, optionally decisions/dead-code/hotspots. | `wiki_pages.json` with page metadata |
| `repowise update` | File diffs, adaptive cascade budget, affected page plan, regenerated/decayed page counts, dead-code/decision refresh results. | `Adaptive cascade budget: 30` |
| `repowise reindex` | Embedding/indexing progress and page counts. | `Indexed 430 items -> .repowise/lancedb` |
| `repowise watch` | Debounced changed-path batches and forwarded update output. | `Detected 3 changed file(s), updating...` |
| `repowise workspace` | Workspace repo discovery, config entries, update status, cross-repo hook output. | `Found 2 new repo(s)` |
| `repowise generate-claude-md` | Editor-file data and rendered `.claude/CLAUDE.md`. | `hotspots`, `key_modules`, `decisions` in markdown |
| `repowise augment` | Hook-time graph/search enrichment for AI tool calls. | Related files, symbols, importers, dependencies |
| `repowise mcp` | FastMCP server exposing the computed graph/wiki/risk tools below. | stdio or SSE transport |

## MCP And API-Visible Computed Payloads

| Tool or endpoint concept | Definition | Example |
| --- | --- | --- |
| `get_answer` | RAG answer with citations, confidence, fallback targets, retrieval metadata, and answer-cache support. | `{answer: "...", confidence: "medium", citations: [...]}` |
| `search_codebase` | Wiki search using vector/FTS and federated workspace RRF when requested. | `{results: [{title, relevance_score, confidence_score}]}` |
| `get_context` | Compact page, symbol, freshness, dependency, git, and cross-repo context for targets. | `{targets: {"src/app.py": {docs, graph, freshness}}}` |
| `get_overview` | Repo or workspace overview, module map, entry points, git health, communities, and workspace footer. | `{summary, modules, git_health, community_summary}` |
| `get_why` | Decision/governance lookup, file origin story, alignment, and decision health modes. | `{decisions: [...], target_context: {...}}` |
| `get_risk` | Per-file risk, trend, risk type, owners, co-change partners, test gaps, security signals, top hotspots, optional PR blast radius. | `{results: [{risk_summary, hotspot_score}], top_hotspots: [...]}` |
| `get_dead_code` | Tiered, grouped, and summarized dead-code findings. | `{summary: {total_findings: 12}, tiers: {...}}` |
| `get_dependency_path` | Dependency-path or bridge context between files/symbols. | `{path: ["src/a.py", "src/b.py"]}` |
| `get_architecture_diagram` | Mermaid architecture diagram text. | `{mermaid_syntax: "graph TD\n..."}` |
| `update_decision_records` | Decision create/update/list/delete payloads. | `{status: "ok", decision: {...}}` |
| `get_symbol` | Exact symbol metadata and source slice. | `{name: "create_app", signature: "def create_app(...)"}` |
| `get_callers_callees` | Caller/callee neighborhood for a symbol. | `{callers: [...], callees: [...]}` |
| `get_graph_metrics` | Centrality percentiles, community, entry-point score, and graph metrics for a node. | `{pagerank_percentile: 92, community_label: "api"}` |
| `get_community` | Community details, cohesion, members, and neighboring communities. | `{label: "auth", cohesion: 0.21, members: [...]}` |
| `get_execution_flows` | Entry-point traces through call edges. | `{flows: [{entry_point, trace, crosses_community}]}` |
| `annotate_file` | Persistent human notes on a wiki page. | `{status: "ok", human_notes: "Watch migration path."}` |
| Blast radius API | Direct risks, transitive affected files, co-change warnings, reviewers, test gaps, overall score. | `{overall_risk_score: 7.25}` |
| Knowledge map API | Top owners, knowledge silos, onboarding targets. | `{top_owners: [...], knowledge_silos: [...]}` |
| Cost summary API | Grouped costs and totals. | `{groups: [...], total_cost_usd: 3.21}` |
| Provider API | Available provider/model configuration. | `{providers: [...], active_provider: "gemini"}` |

## Statuses And Enumerations

| Domain | Values |
| --- | --- |
| Page freshness | `fresh`, `stale`, `expired`, `unknown` in type definitions |
| Job status | `pending`, `running`, `completed`, `failed`, `paused` |
| Decision status | `proposed`, `active`, `deprecated`, `superseded` |
| Decision source | `git_archaeology`, `inline_marker`, `readme_mining`, `cli` |
| Dead-code kind | `unreachable_file`, `unused_export`, `unused_internal`, `zombie_package` |
| Dead-code status | `open`, `acknowledged`, `resolved`, `false_positive` |
| Security severity | `high`, `med`, `low` |
| Security kind | `eval_call`, `exec_call`, `pickle_loads`, `subprocess_shell_true`, `os_system`, `hardcoded_password`, `hardcoded_secret`, `fstring_sql`, `concat_sql`, `tls_verify_false`, `weak_hash`, `security_sensitive_symbol` |
| Edge type | `imports`, `defines`, `calls`, `has_method`, `has_property`, `extends`, `implements`, `method_overrides`, `method_implements`, `co_changes`, `framework`, `dynamic`, plus dynamic subtypes such as `dynamic_uses`, `dynamic_imports`, `dynamic_url_route` |
| Node type | `file`, `symbol`, `external` |
| Search type | `vector`, `fulltext` |
| Contract type | `http`, `grpc`, `topic` |
| Contract role | `provider`, `consumer` |
| Contract link match type | `exact`, `manual` |
| Risk trend | `increasing`, `stable`, `decreasing`, `unknown` |
| Risk type | `bug-prone`, `churn-heavy`, `bus-factor-risk`, `high-coupling`, `stable`, `unknown` |
| Change pattern | `feature-active`, `primarily refactored`, `fix-heavy`, `dependency-churn`, `mixed-activity`, `uncategorized` |
| Chat role | `user`, `assistant` |
| Coordinator health | `ok`, `warning`, `critical` |

## Example End-To-End Computation

For a file `src/auth/session.py`, a typical Repowise index can compute:

1. `FileInfo`: `language="python"`, `is_test=false`, `is_entry_point=false`.
2. `ParsedFile`: symbols such as `src/auth/session.py::SessionStore`, imports such as `from .redis import client`, calls such as `client.get()`.
3. Graph records: a file node, symbol nodes, `defines`, `imports`, `calls`, and maybe `framework` or `dynamic_*` edges.
4. Graph metrics: `pagerank=0.013`, `betweenness=0.004`, `community_id=2`, `community_label="auth"`, `cohesion=0.18`.
5. Git metadata: `commit_count_90d=11`, `primary_owner_name="Asha"`, `temporal_hotspot_score=2.1`, `churn_percentile=0.88`, `is_hotspot=true`.
6. Analysis rows: maybe a security finding `hardcoded_secret`, or a decision record from `# DECISION: store sessions in Redis`.
7. Generated docs: `file_page:src/auth/session.py`, source hash, token counts, summary, freshness, and vector/FTS entries.
8. Risk output: `hotspot_score=0.88`, trend `increasing`, risk type `churn-heavy`, co-change partners, test-gap flag, and an impact surface.

