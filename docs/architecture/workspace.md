# Workspace layer internals

Contributor reference for the multi-repo workspace layer: how contracts are read,
matched and folded into one system graph, what each artifact stores, and how to
extend it. For what users run and see, start with
[scale/WORKSPACES.md](../scale/WORKSPACES.md); for why a given link did or did not
form, [reference/WORKSPACE_CONTRACTS.md](../reference/WORKSPACE_CONTRACTS.md).

All code lives in `packages/core/src/repowise/core/workspace/` unless a path says
otherwise.

## What the layer does

Each workspace repo is indexed on its own, exactly as in single-repo mode, into its
own `.repowise/wiki.db`. The workspace layer runs after those indexes and adds what
no single repo can see: contracts one repo provides and another consumes, package
dependencies between repos, co-change across repos, and a service-level graph that
cycles, metrics, conformance, blast radius and test impact all read.

It reuses ingestion instead of re-deriving it:

| Reused | From | Used for |
|--------|------|----------|
| `FileTraverser` | `ingestion/traverser.py` | The extraction walk, so the scanned set honours `.gitignore` and prunes nested repos like the repo's own index |
| `walk_repo` | `fs_walk.py` | Service-boundary detection and the service-name walk |
| The symbol table in `wiki.db` | `repo_index.py` (`RepoIndex`, `WorkspaceIndex`) | Index-backed routes and typed clients (`extractors/from_index.py`, `extractors/http/index_clients.py`), binding contracts to symbol ids, signature schemas |
| `mask_source` | `ingestion/source_mask.py` | Comment masks for Python and JS/TS |
| `mask_comments_and_strings` | `analysis/security/masking.py` | Comment masks for C-like languages, and the network probe |
| `has_working_tree_changes` | `ingestion/change_detector.py` | Whether a repo's stored contracts can be reused |
| Test reachability | `analysis/test_reachability.py`, `analysis/test_selection.py` | Cross-repo test impact |

The per-repo graph (files, symbols, call edges) is never merged across repos. The
workspace graph is one level up: nodes are services, edges are contracts, package
dependencies and co-changes. Its trust model follows the per-repo rule in
[GRAPH.md](../layers/GRAPH.md#every-edge-says-how-it-got-there): every contract and
link names how it was made, from a closed vocabulary with one confidence per value.

## Module map

| Module | Role |
|--------|------|
| `scanner.py` | Finds git repositories under a directory for `repowise init` |
| `config.py` | `.repowise-workspace.yaml`: repos, `ContractConfig` (each field declares its stage), `ConformanceConfig`, `analysis_fingerprint` |
| `update/` | The workspace update: stale-repo selection and per-repo updates (`workspace_pass.py`, `repo.py`), then the cross-repo phases (`phases.py`) |
| `extractors/` | Contract dialects per type (`http/`, `grpc/`, `topic/`, `socket/`, `data/`), plus `openapi.py`, `masstransit.py`, `service_boundary.py`; layout in [language-support.md](language-support.md#workspace-contract-extraction) |
| `extractors/base.py`, `extractors/dialect.py` | The one walk, `ScanContext` (per-file gate memo and comment mask), `run_dialect`, `DialectExtractor` |
| `code_api.py` | A library's published surface as a `code` contract |
| `origins.py` | `ContractOrigin` and `LinkStrategy`, each with one confidence per value |
| `service_names/` | The service-name map: one module per config source, registry in `__init__.py` |
| `matching/` | The shared exact pass plus per-type passes (`http.py`, `topic.py`, `data.py`), registered in `MATCHERS` |
| `contracts.py` | `run_contract_extraction`, `ContractStore`, reuse and fingerprints, `CONTRACTS_VERSION` |
| `manifests.py`, `maven_dependencies.py`, `cross_repo.py` | Package dependencies by ecosystem, cross-repo co-change, the overlay |
| `system_graph.py` | `build_system_graph`, `SystemGraph.adjacency()` |
| `graph_evidence.py` | `measured` / `partial` / `insufficient` for one graph |
| `network_probe.py` | Counts files that call out but yielded no consumer contract |
| `diagnostics.py` | Per-repo counts, unmatched reasons, coverage |
| `cycles.py`, `conformance.py`, `architecture_metrics.py`, `blast_radius.py`, `breaking_change.py`, `test_impact.py` | The views over the graph and the links |
| `reads.py` | Read projections shared by REST and MCP |
| `store.py` | `workspace.db`, a derived lookup store rebuilt from each repo's `wiki.db` |

## Pipeline

`repowise update --workspace` (alias `repowise workspace update`) updates stale
repos, then `update/phases.py::run_cross_repo_hooks` runs the cross-repo phases. A
workspace with fewer than two repos runs none, and only repos with a `.repowise/`
index take part.

| Step | Code | Output |
|------|------|--------|
| 1. Service boundaries | `extractors/service_boundary.py`, from manifest markers in `manifests.ECOSYSTEMS` | Boundaries per repo, detected once and passed to extraction and the graph |
| 2a. Co-change and packages | `cross_repo.py::run_cross_repo_analysis` | `cross_repo_edges.json` |
| 2b. Contract extraction and matching (runs alongside 2a) | `contracts.py::run_contract_extraction` | `contracts.json` |
| 3. System graph | `system_graph.py::run_system_graph_build` | `system_graph.json`, diagnostics embedded |
| 4. Breaking changes | `breaking_change.py`, the new store against the one on disk before step 2b | `breaking_changes.json` |
| 5. Conformance and cycles | `conformance.py`, `cycles.py` | `conformance.json` |
| 6. Workspace store | `store.py::refresh_workspace_store` | `workspace.db` |

Architecture metrics, blast radius and test impact are computed on read.

### Per-repo extraction

`_extract_one_repo` walks each repo once, over the union of the enabled extractors'
extensions, and hands the file list to every extractor. Each file becomes one
`ScanContext`. Every dialect runs through `extractors/dialect.py::run_dialect`:

1. **Extension**, then **gate**: the file must hold one of the dialect's `requires`
   tokens. `ScanContext.has_any` memoizes each token per file, so a token named by
   several dialects or table rows is searched for once. A contract file format
   (`.sql`, `.prisma`) declares `EVERY_FILE`; `.proto` gates on `service`.
2. **Extract**: the dialect returns contracts built with `build_contract`, which
   takes an `origin` and sets the confidence from it.
3. **Comment rule**: `run_dialect` drops a contract whose line is only a comment.
   The shared match helpers (`call_sites`, `code_matches`, `outside_comments`,
   `consumer_contracts`) skip a match that starts in a comment, so a commented-out
   decorator or mount sets no state. The mask is built the first time a match asks,
   so a file nothing matched in never pays for it.

`DialectExtractor` drives the topic, socket, gRPC and data types. HTTP keeps its own
`HttpExtractor` because it adds two passes (router mounts collected across files,
then index-backed routes and client calls), and each of its dialects still goes
through `run_dialect`. A dialect whose contracts span files offers `repo_pass()`:
it is handed every gated file, then names its contracts once.

After the dialects, `code_api.py` adds published-package contracts,
`merge_openapi_providers` folds an OpenAPI operation into the code route it
describes, `bind_symbol_ids` binds each contract to a symbol in the repo's index,
and `network_probe.count_unrecognised_call_files` counts the files that call out
with no consumer contract read from them. It runs again over `MESSAGE_PROBE_ROWS`
for files on a messaging SDK with no topic contract, kept apart from the HTTP
figure. A topic dialect that locates a call but cannot settle its name records it
with `ScanContext.note_unresolved`, summed into `topic_names_unresolved`.

### Matching

Matching always runs over the whole merged contract set, never a subset. The first
two steps run in `run_contract_extraction` before `matching.run_matching`; manual
links are appended after it.

1. **Service-name map** (`service_names/`): every source walks each repo once and
   reads only files its gate admits, plus the workspace root's own files. Claims
   settle into one `name -> repo` entry; a name claimed for two repos is left
   unmapped. The sources and their rules are in
   [WORKSPACE_CONTRACTS.md](../reference/WORKSPACE_CONTRACTS.md#service-names).
   A server base path (a Spring context-path) settles per repo module instead, and
   `matching/base_paths.py::apply_base_paths` prefixes that module's routes before
   matching, keeping the declared path in `meta` so a re-run recomputes it.
2. **Target resolution** (`matching/http.py::annotate_consumer_targets`): each HTTP
   consumer's host or base token is resolved, in order, through a
   `contracts.service_bases` entry, a repo alias (also the leading label of an
   internal DNS name such as `catalog.svc.cluster.local`), and the service-name map.
   The consumer's `meta` records `target_repo`, `target_via` (the source) and, for a
   map entry, `target_evidence` (the file). A public dotted host nothing maps is
   `external` and never linked. So is a call whose base token and path match a
   `matching/third_party.py` row (`${SUPABASE_URL}/rest/v1/...`) and that nothing
   above placed.
3. **Exact pass** (`matching/__init__.py::_exact_pass`): normalized ids, HTTP method
   and gRPC service wildcards included. Strategy `exact_id`, `pattern_matched` for a
   pattern subscription, `service_mapped` when the provider sits in the consumer's
   resolved target. A target the map placed does not fall back to another repo that
   serves the same path (`common.prefer_target_repo`). An HTTP or gRPC call whose id
   two or more repos serve, with nothing placing it in one, links to none of them
   (`MatchState.refuse`, `meta['ambiguous_provider']`); the HTTP passes keep that rule.
   A package-less gRPC stub id (`grpc::CurrencyService/*`) meets the package-qualified
   proto id (`grpc::shop.CurrencyService/Convert`) on the service's last segment. A proto
   declared in 2+ repos is served only by the repo that registers the service in code
   (`common.shared_idl`); the other copies are vendored IDL, never linked or listed as
   orphans. A .NET project's `<Protobuf GrpcServices="Client">` proto provides nothing.
4. **Type passes**, in `MATCHERS` order: HTTP `gateway_pass` (`gateway_route`),
   `base_resolved_pass`, `candidate_pass` (`unique_path`, `ambiguous_path`, `prefix_stripped`) and `service_pass`; topic
   `binding_pass` (`binding_routed`); data `shared_table_pass` (`shared_table`).
   HTTP's `accepts` (`matching/http.py`) holds every pass to one rule: a generic
   operational path (`/health`, `/metrics`, the root) links only to the consumer's
   resolved target repo, or with none to its own repo.
5. **Service links**: `service_pass` gives each HTTP consumer whose target is a
   workspace repo, but whose path no provider serves, one `host_only` link with no
   provider file. These live in `ContractStore.service_links`, apart from
   `contract_links`. A target that only equals a repo alias draws none. A gateway's
   edge to each service it routes to is a `gateway_route` service link, while a
   consumer call routed through that gateway to a served route is a `gateway_route`
   link in `contract_links`.
6. **Manual links** from `contracts.manual_links` are appended with strategy
   `manual`.

### System graph and its adjacency

`build_system_graph` folds contract links, service links, package dependencies and
co-changes into service nodes and typed edges (`http`, `grpc`, `socket`, `event`,
`db`, `package` are structural; `co_change` is behavioral). Edges sharing source,
target and kind collapse into one weighted edge that keeps the strategy of its
strongest link.

`SystemGraph.adjacency()` is the only place that decides which edges a walk follows:
structural edges in dependency direction by default, `impact=True` for the
blast-radius direction, `behavioral=True` to add co-change both ways. Parallel edges
between one pair stay separate, so a caller can tell an `event` leg
(`ASYNC_EDGE_KINDS`) from a sync one. `cycles.py`, `architecture_metrics.py` and
`blast_radius.py` build no adjacency of their own;
`test_system_graph.py::test_adjacency_is_the_one_view_every_walk_reads` pins its
shape (parallel edges kept, `impact` flips direction, `behavioral` adds co-change
both ways).

### Consumers

| Consumer | Reads | Output |
|----------|-------|--------|
| `cycles.detect_cycles` | `adjacency()` | Cycles with `leg_kinds` per leg and `async_legs`; an event leg still closes a cycle; capped at `MAX_CYCLES`; enumeration stops at `MAX_ENUMERATED_CYCLES`, then the total is a floor (`total_capped`) and the listed cycles come from a length-bounded pass (up to `MAX_CYCLE_LENGTH`) |
| `architecture_metrics.compute_architecture_metrics` | `adjacency()`, `detect_cycles` | Propagation cost, core, roles, 1-10 score |
| `conformance.build_conformance_report` | Structural edges, declared rules | Violations, cycles, `cycles_determined` |
| `blast_radius.cross_repo_blast_radius` | `adjacency(impact=True)`, co-change at `BEHAVIORAL_EDGE_WEIGHT` | Impacted services by distance, `undetermined_targets` |
| `breaking_change` | `contract_links`, previous and new stores | Provider breaks with the consumer files they reach |
| `test_impact.analyze_workspace_test_impact` | `contract_links`, each consumer repo's test reachability from the link's `consumer_symbol_id` | Tests to run, unresolved rows with reasons |
| `diagnostics.build_diagnostics` | Contracts, `contract_links`, extraction stats | Counts, unmatched reasons, weak links, coverage |
| `report estate` (`report/sections/estate_*.py`) | `system_graph.json`, `contracts.json`, `graph_evidence`, the architecture metrics, `workspace.db` health, each member index | Estate summary, system map and architecture, contracts and dangling calls, health and churn per repo, riskiest seams (`seams.rank_seams`), where to start |

Service links reach the graph views (cycles, metrics, conformance, blast radius).
Breaking changes, test impact and diagnostics read `contract_links` only, since a
service link has no provider route.

### Evidence state

`graph_evidence.graph_evidence(graph)` runs once over the graph and returns a state
with named reasons. Each rule is one function in `_RULES`:

| Reason | State |
|--------|-------|
| `too_few_services` | `insufficient` |
| `no_cross_service_edges` | `insufficient` |
| `build_edges_only` (every edge is a `package` edge) | `partial` |
| `isolated_services` | `partial` |
| `repos_without_contracts` (read, yielded nothing) | `partial` |

The state is the worst any reason implies; no reason is `measured`. What each output
does with it:

| Output | `insufficient` | `partial` |
|--------|----------------|-----------|
| Architecture metrics | Score, propagation cost, core size and cycle count are `None`, core members and roles empty; type `undetermined` | Figures computed, reasons attached |
| Conformance, `workspace check` | `cycles_determined: false`; "dependency cycles not determined", exit 0 | "no dependency cycles among the edges detected (partial evidence)" |
| Blast radius | `evidence` attached | `evidence` attached |
| MCP `get_architecture`, `get_blast_radius`, `get_conformance`, REST | Carry the same `evidence` block | Same |
| MCP `get_risk`, `get_change_risk` cross-repo parts | `graph_evidence` with state and reason | Same |

In every state, blast radius lists in `undetermined_targets` each target the walked
adjacency has no edge at, so an empty impact set there reads as not seen.

Test impact reads the links rather than the graph: an empty answer is
`no_contract_links` when the map links nothing and `no_matching_links` when it links
something but not these files. HTTP coverage in diagnostics counts each file the
network probe flagged in its denominator, so a client library no dialect reads
lowers the figure; it is `None` for an artifact written before the probe.

### Persistence

Everything lives in `.repowise-workspace/`:

| File | Holds | Stamp |
|------|-------|-------|
| `contracts.json` | `contracts`, `contract_links`, `service_links`, `service_names`, `extraction_stats`, `repo_provenance` | `CONTRACTS_VERSION`; per repo `head`, `config_fp`, `extracted_at`; the service-name map's `fingerprint` |
| `cross_repo_edges.json` | Co-change pairs, package dependencies, repo summaries | File mtime against each repo's `state.json` |
| `system_graph.json` | Nodes, edges, diagnostics, `repo_provenance` | `version` |
| `breaking_changes.json` | Provider breaks since the previous store | `generated_at` |
| `conformance.json` | Violations, cycles, `evidence` | |
| `analysis_config.json` | The `contracts:` and `conformance:` settings the last analysis ran with | `WorkspaceConfig.analysis_fingerprint()` |
| `member_refresh.json` | One digest per member repo | `update_cmd/member_refresh.py::member_fingerprint` |
| `workspace.db` | Path and symbol lookup, components, recent changes, health, freshness, the per-repo figures `GET /api/workspace` and `/graph` serve | Each repo's `state.json`; the figures also by `wiki.db` stat, read live when it moved; safe to delete |

`member_refresh.py` lives in `packages/cli/src/repowise/cli/commands/update_cmd/`.
Each repo's own `wiki.db` also carries a schema stamp in `PRAGMA user_version`
(`persistence/database.py::schema_stamp`), so opening an unchanged store skips the
schema reconcile.

### Incremental rules

| Change | Re-extracts | Re-matches | Reuses |
|--------|-------------|------------|--------|
| A repo's HEAD moved, its tree is dirty, or upstream marked it changed | That repo | Everything | Every other repo's stored rows |
| An extraction setting (`detect_*`, `exclude_globs`, `contract_project_pattern`), the published-package set or the message-type set | Every repo | Everything | The co-change overlay, when no repo changed |
| A matching setting (`service_bases`, `manual_links`) or a `conformance:` rule | Nothing | Everything | All stored rows; the service-name map when its fingerprint holds |
| `CONTRACTS_VERSION` moved, or no stored contracts | Every repo, once | Everything | Nothing |
| Nothing | Nothing; the cross-repo phases do not run | Nothing | Everything |

Details the table compresses:

- `ContractConfig` fields declare `stage` in field metadata; the extraction
  fingerprint (`_extraction_config_fingerprint`) hashes only extraction settings.
  `test_contract_config.py::test_every_setting_declares_its_stage` fails on a field
  that declares neither.
- Files repowise writes itself (`.claude/CLAUDE.md` and the other managed editor
  files) do not make a tree dirty; a user's edit to one does.
- `WorkspaceIndex.ensure` opens symbol indexes only for re-extracted repos and the
  repos that publish packages they may import.
- The co-change overlay is reused when it covers exactly the current repos and no
  repo was re-indexed after it was written.
- The service-name map's fingerprint covers each repo's HEAD, the workspace root's
  gated files and the registered source names. It is reused only on a run that
  re-extracts no repo.
- A no-change update refreshes read snapshots, editor files and the DB freshness
  stamp only for members whose `member_fingerprint` moved. The digest covers the
  store and its WAL, `state.json`, `config.yaml`, the gitignore stack, the editor
  files, HEAD, the repowise version with the editor generator and templates, and
  the UTC date, so an unchanged repo still refreshes once a day. A configured
  database (`REPOWISE_DB_URL`) and an explicit `--agents-md` always refresh; a
  failed refresh is not stamped.

## Seams and registries

| Registry | Where | An entry declares | Enforced by |
|----------|-------|-------------------|-------------|
| HTTP dialects | `extractors/http/__init__.py` `PROVIDER_DIALECTS`, `CONSUMER_DIALECTS` | `name`, `extensions`, `requires`, `extract` | `test_dialect_registry.py`, `test_extraction_dialect_budget.py` |
| Topic, socket, gRPC, data dialects | `DIALECTS` in `extractors/topic/`, `socket/`, `grpc/`; `PROVIDER_DIALECTS` and `CONSUMER_DIALECTS` in `data/` | The same; a table dialect's rows each declare `requires` and an `origin`, and the dialect's gate is the union of its rows' (`rows_gate`) | The same two, plus `test_origins.py` |
| Contract origins | `origins.py` `ContractOrigin`, `ORIGIN_CONFIDENCE` | One confidence per value | `test_origins.py` |
| Link strategies | `origins.py` `LinkStrategy`, `STRATEGY_CONFIDENCE` | One factor per value; `match_type_for` derives exact, candidate or manual | `test_origins.py` |
| Type matchers | `matching/__init__.py` `MATCHERS` | Optional `deferred`, `accepts`, `keys`, `passes` | Per-type matching tests |
| Service-name sources | `service_names/__init__.py` `SOURCES`, sorted by name | `name`, `filenames`, optional `under` and `marker`, `read` | `test_service_names.py` |
| Evidence rules | `graph_evidence.py` `_RULES` | One function returning a reason or `None` | `test_graph_evidence.py` |
| Network probe | `network_probe.py` `PROBE_ROWS`, sorted by language | `language`, `extensions`, `tokens`, `hash_comments` | `test_network_probe.py` |
| Third-party services | `matching/third_party.py` `THIRD_PARTY_SERVICES`, sorted by name | `name`, `base_words`, `paths` | `test_third_party_services.py` |
| Edge kinds | `system_graph.py` `_CONTRACT_TYPE_TO_EDGE_KIND` | Contract type to edge kind | `test_system_graph.py::test_contract_type_to_edge_kind` |
| Package ecosystems | `manifests.py` `ECOSYSTEMS` | Service markers and an optional manifest scanner | `test_manifests.py` |

What the registry tests assert:

- `test_dialect_registry.py`: every registered dialect has a non-empty gate, and
  `EVERY_FILE` only on contract formats (`.proto`, `.sql`, `.prisma`); every table
  row has a gate and an `origin` from `ContractOrigin`; names are unique per
  contract type; a token is searched for once per file however many dialects name
  it.
- `test_extraction_dialect_budget.py`: every source-code dialect costs at most 40%
  of one identifier-led regex pass over the same text, timed in the same process.
- `test_service_names.py`: `SOURCES` is sorted and unique, every module's `SOURCE` is
  registered, a broad glob (`*.yaml`) carries `under` or a `marker`, and only gated
  files are opened.
- `test_network_probe.py`: rows are sorted and unique by language, no extension
  belongs to two rows, and every token compiles.
- `test_extraction_walk_budget.py`: one tree walk per repo, each file read once.

## Trust model

A contract's confidence is its origin's; a link's is its weaker end's times its
strategy's factor (`origins.link_confidence`). The value tables, with what each
origin and strategy establishes, are in
[WORKSPACE_CONTRACTS.md](../reference/WORKSPACE_CONTRACTS.md#how-a-contract-and-a-link-were-made).
Rules that follow from them:

- A dialect picks an existing origin; it never sets a number. A new way of
  establishing a contract adds one `ContractOrigin` row.
- `match_type` is derived from the strategy, never set on its own. Candidate
  strategies are `ambiguous_path`, `prefix_stripped` and `host_only`.
- A system-graph edge carries the strategy of the link that gave it its confidence,
  `manifest` for a package edge, and none for co-change.
- A stored link without a strategy is read through `legacy_strategy`; that store's
  version is older, so the next update re-extracts it.
- Diagnostics reports links at or below `WEAK_LINK_CONFIDENCE_THRESHOLD` (0.4) as
  weak.

The evidence state is the graph-level counterpart: a link's confidence says how far
to trust one edge, and `graph_evidence` says whether the set of edges is enough to
support a score, a cycle verdict or an empty impact set.

## Performance model

- **One walk per repo** for extraction, over the union of the enabled extractors'
  extensions, shared by every extractor. Service-name sources share a second, pruned
  walk that reads only gated config files.
- **Gates once per file.** A dialect's gate is a substring test memoized per token in
  `ScanContext`, so adding a dialect that names known tokens adds no search.
  Ungated dialects fail the registry test, and a dialect that costs as much as an
  ungated scan fails the budget test.
- **Comment masks on demand.** Built once per file, the first time a match asks.
- **Network probe**: one compiled alternation per extension, one search per file that
  produced no consumer; the comment mask is paid only on a hit.
- **Matching is dict-indexed** and runs over the whole set every time; it costs
  milliseconds against seconds of extraction.
- **Graph views are linear** in nodes and edges; cycle enumeration runs on a
  service-granular graph and is capped.
- **A no-change update** runs no cross-repo phase and touches no unchanged member.
  What remains is module import and the per-repo HEAD and staleness probes.

## Contributor recipes

### Add a contract dialect

1. Pick the contract type and create one module in `extractors/http/`, `topic/`,
   `socket/`, `grpc/` or `data/`. A broker or client that fits an existing table
   shape (`TopicCall`, `SocketCall`, the gRPC stub rules in `grpc/languages.py`) is
   rows in a table, not new code. A client whose methods are too common to read on
   any receiver (`send`, `produce`) is a `ReceiverCall` row: read on the names the
   file declares with the client's type.
2. Declare `name` and `extensions` (from `extractors/langs.py`).
3. Declare `requires`: tokens one of which every match needs, such as the library's
   import or the decorator's name. Each table row declares its own. Use
   `EVERY_FILE` only for a contract file format.
4. Declare an `origin` from `ContractOrigin` on each row, or pass one to
   `build_contract` (HTTP: `build_provider_contract` / `build_consumer_contract` in
   `http/dialect.py`; data: `build_table_provider` / `build_table_consumer` in
   `data/dialect.py`). Pick the value whose description matches what established
   the contract.
5. Read through the shared helpers (`calls.call_sites`, `calls.code_matches`,
   `strings.Arg`) so the comment rule and constant resolution apply. Do not add a
   comment check, constant table or path normalizer of your own.
6. If contracts span files, offer `repo_pass()` returning a `RepoPass`.
7. Append the dialect to its type's registry tuple. Order matters only where two
   dialects read one call alike: the first contract per file, id and role is kept.
8. If the two ends can name one thing differently, add a pass or `keys` rule to the
   type's `TypeMatcher` in `matching.MATCHERS`.
9. Add the dialect's tests beside its type's (`test_topic_dialects.py`,
   `test_socket_dialects.py`, `test_framework_dialects.py`,
   `test_data_contracts.py`, the `test_*_clients.py` files): a real snippet that
   yields the contract, one that must not (a lookalike call, a commented-out use),
   and the origin. Run `test_dialect_registry.py`, `test_dialect_comments.py`,
   `test_extraction_dialect_budget.py` and `test_origins.py`.
10. Add the library to the support matrix in
    [WORKSPACES.md](../scale/WORKSPACES.md#api-contract-extraction). If the change
    alters stored rows, check `git log` on `contracts.py` since the last release tag
    before touching `CONTRACTS_VERSION`: it moves at most once per release.

### Add a service-name source

1. Create one module in `service_names/` that defines `read(file: SourceFile)`
   yielding `Claim` rows, and a module-level `SOURCE = ServiceNameSource(...)`.
2. Declare the gate: `filenames` (case-insensitive globs on the file name), plus
   `under` (ancestor directory names) or `marker` (text that must occur) when a glob
   could admit ordinary files. Parse with `yaml_docs.load_yaml_docs` for YAML.
3. In each `Claim`, say where the name points: `at` (the directory the service is
   built from), `aliases` (repo aliases the file names, such as an image basename),
   `ref` (an `ENV` key whose value is a host, resolved through the finished host
   map) or `own_repo` (the repo holding the file, kept only when it is the only name
   the source places there). Use `base.host_of` to read address values. A gateway
   states `route=Route(prefix, upstream)` claims instead, with `base.literal_prefix`
   for its path patterns; the engine settles them into `GatewayRoute` rows the HTTP
   matcher applies (`matching/http.py::gateway_pass`).
4. Add `SOURCE` to `SOURCES`, keeping it sorted by `name`.
5. Copy a real config file into `tests/fixtures/service_names/<estate>/`, with
   `.keep` files for the repo directories it names, and add a test in
   `test_service_names.py` asserting the names it maps and the ones it must not
   (a test-resource file, a template, a name shared by two repos).
6. Add a row to the sources table in
   [WORKSPACE_CONTRACTS.md](../reference/WORKSPACE_CONTRACTS.md#service-names).

### Add a probe row or an evidence rule

A client library the probe should count is one token in its language's
`PROBE_ROWS` entry; a new language is one row. A new kind of missing evidence is one
function in `graph_evidence._RULES` returning an `EvidenceReason` with its state;
add its case to `test_graph_evidence.py`.

## Limits

- Only Python, JS/TS, Java, Kotlin, Go, C#, Rust, PHP, Apex, Proto and
  `.prisma` files have a comment mask (see [`_SLASH_COMMENT_SUFFIXES` and
  `_comment_mask`](../../../packages/core/src/repowise/core/workspace/extractors/base.py));
  every other suffix is read as written, so a commented-out match there is
  read. A Python docstring example is read, because the mask keeps strings.
- A gate is a substring test: it keeps a dialect off files it can find nothing in,
  not off every file that mentions its token.
- Service names are workspace-wide, not scoped per compose project or cluster. Two
  stacks reusing one name collide and the name is dropped. Helm templates and
  `ExternalName` services are not read.
- Only HTTP consumers resolve targets through the map, and only HTTP has service
  links.
- Gateway routes are prefix routes only. A relative call from a frontend repo other
  than the gateway's is not routed, since nothing says which gateway serves it.
  nginx regex locations are not read, and `${VAR}` resolves as an env key only in a
  `*.template` / `*.tmpl` file (envsubst); in a plain config it is an nginx variable.
- YARP routes built in code (`LoadFromMemory`, Aspire `AddYarp`) are not read, and
  `MapForwarder` only in `Program.cs` / `Startup.cs`.
- A `host_only` edge is service-level: it has no provider route, so breaking-change
  diffs and test impact do not follow it.
- The network probe is lexical. It reads only extensions some extractor walks,
  counts a token inside a string literal, counts each file once whatever its call
  count, and a file whose calls were located but unresolved can be counted both
  there and as unresolved.
- Evidence is read from the graph alone. `build_edges_only` treats every `package`
  edge as a build reference.
- A member's refresh digest does not read the working tree: an uncommitted edit the
  editor generator reads live shows at the next commit, index write or day.
- An extraction setting edit re-extracts every repo.
- Data contracts have no field-level diff, and OpenAPI comparison has a fixed
  boundary; both are stated in
  [WORKSPACE_CONTRACTS.md](../reference/WORKSPACE_CONTRACTS.md#data-contracts).

## See also

- [scale/WORKSPACES.md](../scale/WORKSPACES.md): commands, configuration, the system graph and every view, as users see them
- [reference/WORKSPACE_CONTRACTS.md](../reference/WORKSPACE_CONTRACTS.md): origin and strategy tables, service names, per-transport matching rules
- [language-support.md](language-support.md#workspace-contract-extraction): the extractor tree and the framework matrix
- [layers/GRAPH.md](../layers/GRAPH.md): the per-repo graph and the origin rule this layer follows
- [test-intelligence.md](test-intelligence.md): the test reachability cross-repo test impact joins against
