# Workspaces, Multi-Repo Support

A workspace indexes several repositories together. Each repo keeps its own docs, graph and search, and the workspace adds cross-repo analysis: co-change, API contracts matched provider to consumer, package dependencies, a service-level system graph, breaking-change and test-impact checks, and architecture rules.

---

## Table of Contents

1. [When to Use Workspaces](#when-to-use-workspaces)
2. [Quick Start](#quick-start)
3. [How It Works](#how-it-works)
4. [Workspace Commands](#workspace-commands)
5. [Cross-Repo Intelligence](#cross-repo-intelligence)
6. [External Systems](#external-systems)
7. [System Graph](#system-graph) and [Extraction Diagnostics](#extraction-diagnostics)
8. [Web UI](#web-ui) and [Live System Map](#live-system-map)
9. [Cross-Repo Blast Radius](#cross-repo-blast-radius), [Breaking-Change Guard](#breaking-change-guard), [Cross-Repo Test Impact](#cross-repo-test-impact)
10. [Architecture Conformance](#architecture-conformance) and [Architecture Metrics](#architecture-metrics)
11. [MCP Integration](#mcp-integration)
12. [File Layout](#file-layout)
13. [FAQ](#faq)

---

## When to Use Workspaces

Use a workspace when your project spans multiple git repositories that are related:

- A **backend + frontend** in separate repos
- A **monorepo root** with standalone service repos alongside it
- **Microservices** that communicate over HTTP, gRPC, or message topics
- Any set of repos where you want to understand **cross-repo dependencies and co-change patterns**

With a single repo, `repowise init` needs no workspace.

---

## Quick Start

Put related repos under a common parent directory. The parent can itself be a
git repo (a monorepo with sub-repos).

```
my-workspace/
  backend/          # git repo
  frontend/         # git repo
  shared-libs/      # git repo
```

```bash
cd my-workspace
repowise init .
```

`repowise init .` scans for git repositories up to 3 levels deep, asks which to
index and which is the primary repo (the default for MCP queries), walks you
through provider setup, then indexes each repo, generates its docs (unless
`--no-prose`), runs the cross-repo analysis, and registers the MCP server with
your editors.

```bash
repowise status --workspace           # workspace status
repowise workspace list               # repos and index state
repowise serve                        # web UI
repowise search "authentication flow" # search across repos
```

---

## How It Works

A workspace is a directory containing multiple git repositories, tied together by a config file (`.repowise-workspace.yaml`) and a shared data directory (`.repowise-workspace/`).

Each repo is indexed independently into its own `.repowise/wiki.db`, the same format as single-repo mode. The workspace layer adds cross-repo analysis on top.

### Single-Repo vs Workspace

| Feature | Single-Repo | Workspace |
|---------|-------------|-----------|
| Per-repo docs, graph, search | Yes | Yes (for each repo) |
| Co-change detection | Within repo | Within + across repos |
| API contract extraction | No | Yes (HTTP, gRPC, topics, sockets, data) |
| Package dependency mapping | No | Yes |
| Web UI | Repo pages | Repo pages + workspace dashboard |
| MCP | One server per repo | One server, all repos |

---

## Workspace Commands

Run these from anywhere inside the workspace. Flags and examples for each are
in the [CLI reference](../reference/CLI_REFERENCE.md#workspace-commands).

| Command | What it does |
|---------|--------------|
| `repowise init .` | Initialize the workspace: scan for repos, select, index (`--no-prose` for a free structure-only wiki, `-x` to exclude globs) |
| `repowise workspace list` | Repos in the workspace with their index status |
| `repowise workspace add <path>` | Add and index a repo (`--alias`, `--no-docs`, `--no-index`) |
| `repowise workspace remove <alias>` | Remove a repo from the workspace (files are not deleted) |
| `repowise workspace scan` | Find repos not yet added |
| `repowise workspace set-default <alias>` | Change the default repo for MCP queries |
| `repowise workspace diagnostics` | Explain the contract link count ([Extraction Diagnostics](#extraction-diagnostics)) |
| `repowise workspace check` | Architecture lint; exits non-zero on findings ([Architecture Conformance](#architecture-conformance)) |
| `repowise workspace metrics` | Propagation cost, cyclic core, service roles, 1-10 score ([Architecture Metrics](#architecture-metrics)) |
| `repowise workspace impacted-tests <repo:path>...` | Tests in consumer repos to run for a provider change ([Cross-Repo Test Impact](#cross-repo-test-impact)) |

The report commands take `--format table|json` (`impacted-tests` also takes
`list`); `--json` is a deprecated alias.

---

## Cross-Repo Intelligence

When a workspace has two or more repos, repowise runs three kinds of cross-repo analysis. Each repo also gets its own [external systems](#external-systems) registry.

### Co-Change Detection

Analyzes git history across repos to find files that frequently change together. For example, if `backend/api/routes.py` and `frontend/src/api/client.ts` are always modified in the same time window, they get a high co-change score.

Use it to find implicit dependencies between repos, such as the frontend files
to check when a backend API changes.

### API Contract Extraction

Scans source files for HTTP routes, gRPC services, database tables, message topics and socket events. Then matches providers (servers) with consumers (clients) across repos.

**HTTP routes and calls:**

| Language | Providers (routes served) | Consumers (calls made) |
|----------|---------------------------|------------------------|
| JS / TS | Express, Hono, Fastify, Koa, Elysia; NestJS controllers; Next.js App Router; Remix | `fetch`; axios, ky, got, ofetch and their instances; Angular `HttpClient`; HTTP-named wrappers |
| Python | FastAPI, Flask, Django | requests, httpx (aiohttp through wrappers the index confirms) |
| PHP | Laravel | Guzzle, Laravel `Http` |
| Java / Kotlin | Spring, JAX-RS, Micronaut | Feign, `java.net.http`, `RestTemplate`, Ktor (Kotlin) |
| Go | gin, echo, chi, net/http | net/http |
| C# | ASP.NET (attribute and minimal API) | HttpClient, UnityWebRequest, Best.HTTP |
| Ruby | | HTTParty, RestClient, Faraday, Net::HTTP |
| Rust | Axum, Actix, Rocket | reqwest |
| Any | OpenAPI 3.x documents | |

**Database tables:**

| Language | Providers (tables declared) | Consumers (tables queried) |
|----------|-----------------------------|----------------------------|
| SQL | `CREATE TABLE` / `VIEW` / `MATERIALIZED VIEW`, `ALTER TABLE` | |
| JS / TS | Prisma, TypeORM, Sequelize, Drizzle, Knex migrations | SQL strings, Knex queries |
| Python | SQLAlchemy, SQLModel, Django, Alembic | SQL strings |
| PHP | Eloquent, Laravel migrations | SQL strings, `DB::table` |
| Java / Kotlin | JPA | SQL strings |
| Go | | SQL strings |
| C# | EF Core | SQL strings |
| Ruby | ActiveRecord | SQL strings |

**gRPC, topics and sockets:**

| Type | Providers | Consumers |
|------|-----------|-----------|
| gRPC | `.proto` service definitions, plus per-language dialects (Go, Java, Python, C#, TypeScript, NestJS `@GrpcMethod`) | gRPC client stubs |
| Topics | Kafka (Spring Kafka, kafkajs, kafka-python/confluent, sarama), RabbitMQ (Spring AMQP, amqplib, pika, php-amqplib), NATS, Redis pub/sub (ioredis/node-redis, redis-py, Laravel `Redis::publish`), BullMQ / Bull (`new Queue`, `@InjectQueue`, flows), SQS and SNS (AWS SDK v2/v3, boto3), NestJS `ClientProxy.emit`/`send`, Laravel job dispatch (`X::dispatch()->onQueue()`, `dispatch()`, `Queue::push*`, scheduled jobs) | The corresponding consumers (`new Worker`, `@Processor`, `ReceiveMessageCommand`, sqs-consumer, `subscribe`/`psubscribe`, `@EventPattern`/`@MessagePattern`, Laravel `ShouldQueue` classes on the queues they run on), plus RabbitMQ queue bindings (`bindQueue`, `queue_bind`) |
| Socket / WebSocket | SignalR `MapHub<T>("/path")`, FastAPI `@app.websocket("/path")`, `ws` `WebSocketServer({ path })`, NestJS `@WebSocketGateway`; events: socket.io `emit` (server and client), Laravel broadcast events (`broadcastOn` / `broadcastAs`), `Broadcast::on`, Pusher `trigger` | ClientWebSocket `ConnectAsync`, SignalR `HubConnectionBuilder.WithUrl`, NativeWebSocket and WebSocketSharp `new WebSocket(...)`, browser/Node `new WebSocket(url)`; events: socket.io `on` / `@SubscribeMessage`, Laravel Echo `listen` and `useEcho`, pusher-js `bind` |

Socket detection is toggled by `detect_socket` in the `contracts:` block below.
A topic, queue or route name is resolved when it is a literal or a constant the
same file assigns once; a name built at runtime is skipped, not guessed. HTTP
routes match on their full path, with router mount prefixes stitched on first.
An ambiguous target becomes a lower-confidence **candidate** link, and a unique
one an **exact** link. The exact naming and matching rules for each transport
(socket scopes, pattern subscriptions, RabbitMQ bindings, Laravel queues, ORM
table defaults, framework route prefixes, client base URLs) are in the
[contract matching reference](../reference/WORKSPACE_CONTRACTS.md).

Data/DB contracts use the id scheme `data::<table>` and render as a `db` edge in
the [system graph](#system-graph). The consumer side (SQL string matching) is
heuristic and lower-confidence than the ORM-based providers, and data contracts
get table-level removal checks only, no field-level diffing.

Over REST, `GET /api/workspace/contracts` lists contracts and links (filter by
`contract_type`, `repo`, `role`), and `GET /api/workspace/contracts/detail`
returns one contract with its schema.

**Tuning extraction** via the `contracts:` block in `.repowise-workspace.yaml`:

```yaml
contracts:
  detect_http: true
  detect_grpc: true
  detect_socket: true
  detect_topics: true
  detect_data: true
  # Map a consumer base token or absolute host to the repo it targets, so a
  # call whose base is unresolved at parse time links as an exact match.
  service_bases:
    API_BASE: backend          # ${API_BASE}/... -> the "backend" repo
    api.example.com: backend    # https://api.example.com/... -> "backend"
  # Extra globs to skip (added to the built-in test/spec defaults).
  exclude_globs:
    - "generated/**"
```

Directories named `tests/`, `__tests__/`, and `__mocks__/` are excluded by name;
`test/`, `spec/`, and `e2e/` are deliberately *not* excluded by directory, since those
names double as legitimate product directories in some codebases. Regardless of
directory, filenames matching `test_*.py`, `*_test.py`, `*_test.go`, `*.test.*`,
`*.spec.*`, `*.e2e.*`, or `conftest.py` are always excluded: a route or topic that
exists only in a test is a fixture, not a service contract. Calls to a literal
third-party host (Stripe, Formspree, ...) that is not a workspace service are
excluded from matching and reported under the `external_host` diagnostics reason.

### Package Dependency Scanning

Reads package manifests (`package.json`, `composer.json`, `pyproject.toml`,
`Cargo.toml`, `go.mod`, `.csproj`, and Maven `pom.xml`) to detect when one repo
depends on another as a package or project.

npm and composer dependencies match through a local path that resolves into a
sibling repo, or a package name that exactly one sibling repo publishes; a name
two repos publish links to neither. Maven matching is filesystem-only: it links
an active direct compile/runtime dependency when exactly one workspace project
publishes that `groupId:artifactId`, and never runs Maven or downloads
artifacts. Details: [contract matching reference](../reference/WORKSPACE_CONTRACTS.md#package-matching).

---

## External Systems

Every indexed repo, in a workspace or not, keeps a registry of the third-party
packages it declares. repowise reads dependency manifests during indexing; no
package is downloaded and no build tool runs.

| Ecosystem | Manifests read |
|-----------|----------------|
| npm | `package.json` |
| PyPI | `pyproject.toml` (PEP 621, optional dependencies, Poetry groups), `requirements*.txt` |
| Cargo | `Cargo.toml` |
| Go | `go.mod` |
| NuGet | `*.csproj` |
| Maven | `pom.xml` |
| CMake | `find_package(...)` calls in `CMakeLists.txt` |

Each declaration keeps its name, version, the manifest it came from, and whether
it is a dev dependency. A dependency on the repo's own packages is dropped: it
is part of the codebase, not an external system. Two tags are added from
built-in name lists:

- **Category**: `framework` (fastapi, react, spring, gin), `service` (a network
  dependency such as stripe, openai, an AWS or Azure SDK, a database driver),
  `tool` (eslint, pytest, vite) or `library`, the default. The lists are
  deliberately small, so an unknown package stays `library`.
- **I/O kind**: `db`, `network`, `filesystem`, `subprocess` or `lock`, when the
  package is a known boundary of that kind. Unknown names have none.

The registry also links each package to the import edges that reach it, so you
can see which parts of the codebase use it. In the dashboard this is the
**Packages** tab on a repo's Architecture page: declared packages with their
importing communities, and a drill-down to the importing files. The C4 view
uses the same registry to draw external boxes. Over REST:
`GET /api/repos/{repo_id}/external-systems/summary`.

This is a declared-dependency view. It does not resolve transitive
dependencies or lockfiles, and it does not match a package to a sibling
workspace repo; that is [Package Dependency Scanning](#package-dependency-scanning).

---

## System Graph

The contracts, package dependencies, and co-changes above are each a flat list. repowise folds them into a single normalized **system graph**, the one structure every cross-repo view reads. It is rebuilt automatically on every `repowise update --workspace` and persisted to `.repowise-workspace/system_graph.json`.

**Nodes are services, not repos.** A monorepo with three detected service boundaries (a `package.json` / `composer.json` / `go.mod` / `Cargo.toml` sub-directory) shows three nodes; the repo is a grouping attribute on each node. A repo with no sub-boundary collapses to a single repo-root node. Each node carries its provider/consumer counts, the contract types it participates in, and flags for orphan/isolated services.

**Edges are typed.** Every edge carries:

- a `kind`, `http`, `grpc`, `event`, `package`, `co_change`, or `db`;
- a `match_type`, `exact`, `candidate`, `manual`, or `inferred`;
- a `confidence` and a `weight` (how many underlying contracts / deps / co-changes it aggregates);
- `contract_refs` back-pointers so any view can drill from an edge to its evidence.

Edge direction is uniform: **`source` depends on / calls `target`.** A consumer points to the provider it calls; a dependent repo points to the repo it imports. Structural edges (contracts, package deps) are flagged distinctly from behavioral co-change edges, repowise never conflates "these change together" with "these call each other".

Fetch it over REST with `GET /api/workspace/system-graph`, or explore it visually in the [Live System Map](#live-system-map).

## Extraction Diagnostics

When the cross-repo link count looks low, diagnostics explain why. Computed alongside contract matching, they report, per repo and contract type, how many providers and consumers were found, which consumers went unmatched (and why), and which providers have no consumer at all.

```bash
repowise workspace diagnostics                # human-readable report
repowise workspace diagnostics --format json  # raw JSON
repowise workspace diagnostics --repo api     # limit to one repo
```

The report covers:

- **Provider / consumer counts** per repo, broken down by contract type.
- **Unmatched consumers**, grouped by reason:
  - `no_provider`, no provider anywhere declares a matching route/service/topic.
  - `internal_only`, the only matching provider is in the same repo + service, so the call is intra-service and intentionally not surfaced as a cross-repo link.
  - `unlinked`, a cross-service provider with a matching id exists, but no link formed (a candidate worth inspecting).
  - `external_host`, the call targets a literal third-party host (Stripe, Formspree, ...) that is not a workspace service, so it is intentionally excluded from matching.
- **Orphan providers**, endpoints declared but never consumed by any repo.
- **Weak links**, matched links below the confidence threshold.
- **Extraction coverage**: how many contracts came from the parsed symbol table (`index`) versus a text dialect (`regex`), and how many HTTP client calls were located but could not be resolved to an endpoint. The HTTP coverage percentage is calls resolved over calls located. It is not total recall, because a call no dialect recognises is not in either number. It is the figure that turns a large orphan-provider count from alarming into explained.

The same data is available over REST at `GET /api/workspace/diagnostics` and is embedded in the system graph artifact's `diagnostics` block.

---

## Web UI

Start the web server:

```bash
repowise serve
```

In workspace mode, the web UI adds:

- **Workspace Dashboard** (`/workspace`), aggregate stats across all repos, repo cards with file/symbol/coverage counts, and cross-repo intelligence summary
- **System Map** (`/workspace/system-map`), the [Live System Map](#live-system-map): a code-derived diagram of services and their typed relationships
- **Contracts View** (`/workspace/contracts`), all detected API contracts with provider/consumer matching, filterable by type and repo
- **Co-Changes View** (`/workspace/co-changes`), cross-repo file pairs ranked by co-change strength

The sidebar shows all workspace repos under **Repositories**. Click any repo to access its full per-repo pages (overview, docs, graph, search, hotspots, etc.).

### Live System Map

The System Map renders the [system graph](#system-graph) as an always-current diagram. It is the visual counterpart to the REST endpoint, the same nodes and edges, laid out and explorable, never a hand-drawn picture.

- **Service nodes**, coloured by category (service, frontend, worker, library, external), with a health ring rolled up from the owning repo and small flags for orphan or isolated services.
- **Typed edges** distinguished by `kind` (colour + glyph) and by `match_type` (solid for exact/manual, dashed for candidate, dotted for inferred co-change). Behavioral co-change edges read differently from structural contract/dependency edges.
- **Filters** to toggle each edge kind on or off, and a **service / repo** switch that collapses a monorepo's services into one node per repository.
- **Drill-down**: click a service to inspect its providers/consumers and connected services; click an edge to see its match type, confidence, weight, and the underlying contract evidence, with a jump to the Contracts view.
- A **legend** explaining the edge colours, dash patterns, and the health scale.

The map appears once the workspace has at least two indexed repositories with detected relationships; it shows an empty state otherwise.

---

## Cross-Repo Blast Radius

Blast radius answers a single question: **if I change this service, which downstream services and repos are structurally exposed?** It walks the [system graph](#system-graph) *against* its edge direction (a consumer-to-provider edge means changing the provider may impact the consumer) and returns every reachable service ranked by an impact score.

Two edge classes are weighted and labelled distinctly:

- **Structural** edges (http / grpc / event / package / db) assert a real dependency, a contract or an import. They propagate impact at full weight and surface under the compatibility-named **will break** field, but mean structural reach, not certain runtime failure.
- **Behavioral** co-change edges only assert that two files historically *changed together*. They are correlation, not a call, so they propagate at half weight and surface as **may drift**.

Each impacted service carries its `distance` (hops from the change) and `score` (0-1, with distance decay and the behavioral weighting baked in). Nearer, structural impact ranks highest.

Use it three ways:

- **REST**, `GET /api/workspace/blast-radius?target=<node-id-or-repo>&max_depth=3&include_behavioral=true`. `target` is a node id (`repo` or `repo::service/path`) or a repo alias (expands to all its services).
- **MCP**, the opt-in `get_blast_radius` tool (workspace mode, `mcp.tools: ["+get_blast_radius"]`) gives an agent the impacted set before it touches a high-fan-out provider. The `get_risk` PR-mode directive also gains `will_break_consumers` and `missing_cross_repo_cochanges` so a diff in one repo flags its cross-repo fallout.
- **System Map**, pick a service in the **Blast radius** control above the map; the reachable set ripples (highlighted, the rest dimmed, badges grading intensity), and a side panel lists the impacted services. Click any impacted service to walk the impact outward from there.

---

## Breaking-Change Guard

Where blast radius answers *what could be affected*, the breaking-change guard answers a sharper question: **did a provider contract change incompatibly?** On every `repowise update --workspace`, freshly extracted contracts are diffed against the previously indexed set. Each finding carries the consumer files linked to the endpoint, but that link proves endpoint exposure only. It does not prove use of the changed field, a runtime failure, or deployment safety.

Detected change kinds:

| Kind | Severity | Fires when |
|------|----------|-----------|
| `removed_endpoint` | breaking | A provider route / gRPC method / topic that existed before is gone |
| `removed_field` | breaking (response) / warning (request) | A request or response field disappeared |
| `field_type_changed` | breaking | A field's type changed (e.g. `string -> int64`) |
| `field_number_changed` | breaking | A proto field's wire number changed |
| `field_required` | breaking | A request field became required, or a new required request field was added; legacy proto/signature behavior is preserved |
| `field_required_relaxed` | breaking | A required OpenAPI response field became optional |
| `field_nullability_changed` | breaking | An OpenAPI request stopped accepting null, or a response started allowing null |
| `field_enum_changed` | breaking | An OpenAPI request enum lost values, or a response enum gained values |
| `schema_comparison_uncertain` | warning | Schema source/fidelity, completeness, or selected response changed, so field compatibility was not inferred |

Field-level comparison covers proto, signatures and a supported subset of
OpenAPI `3.0.x` to `3.2.x` (JSON request bodies, one JSON 2xx response, objects,
arrays, primitive types, requiredness, nullability, scalar enums, same-document
references). Anything outside that subset becomes a warning-level
`schema_comparison_uncertain`, never a false removal. The exact boundary:
[contract matching reference](../reference/WORKSPACE_CONTRACTS.md#openapi-comparison-boundary).

**Compatible changes stay quiet**: examples include an optional request addition, a request enum widening, a request becoming nullable, a response enum narrowing, a response becoming non-nullable, an additional response field in the supported open-object subset, and a brand-new endpoint. A rename remains a removal plus an addition; no rename inference is attempted.

Endpoint-exposed consumers are resolved from the matched contract links, the same provider-consumer evidence the [system graph](#system-graph)'s edges are built from. The evidence is direct and endpoint-level: it identifies a consumer file linked to the changed contract, but not the exact field it uses. Transitive structural reach stays the job of blast radius.

Use it three ways:

- **REST**, `GET /api/workspace/breaking-changes` returns the report from the most recent update (filterable by `repo` or `severity`). Each change carries its provider, detail, and impacted consumers with both code sides.
- **MCP**, the `get_risk` PR-mode directive's compatibility-named `breaking_changes` block lists provider incompatibilities and comparison warnings with side, source/fidelity, and endpoint-exposed consumers across repos.
- **System Map**, toggle **Breaking changes** above the map: changed providers are badged by severity. Consumers and seams are marked *exposed* only for provider incompatibilities; warning-only uncertainty is never rendered as a consumer failure claim. A side panel lists each finding with both code sides.

---

## Cross-Repo Test Impact

Where the breaking-change guard identifies provider incompatibility and endpoint exposure, test impact answers the question you ask before you push: **I changed this provider file, which tests in the other repos should I run?** It starts from the same matched contract links and then walks each consumer's own index to find tests that exercise the call site. A recommended test validates the exposed consumer path; it is not proof that the changed field is used or that a deployment will fail.

```bash
repowise workspace impacted-tests backend:app/routers/users.py
```

Three output formats: `table` (the default, grouped by consumer repo), `json` (the full result, including the counts below), and `list` (one `repo:test-file` per line, for piping into a test runner).

Every consumer call site the walk considers ends in one of four states, and the command names the one it landed on:

- **measured**, a coverage map ingested from that repo says the test actually ran the consumer code. The strongest evidence, and it only exists where coverage has been ingested.
- **inferred**, no coverage, but the consumer's call graph or import graph reaches the call site from a test. The call graph is entered at the *symbol* the contract bound to, not the file, so a test that reaches an unrelated function in the same file is not recommended. The import fallback only knows files, so it is entered at the file; every row says which it was, in the `entry` field of its evidence.
- **none**, the consumer was analyzed and nothing reaches the call site. A real answer, not a failure: that code has no test guarding it.
- **unresolved**, the join could not determine an answer. Four causes, each reported by name: the consumer repo has no index, the contract never bound to a symbol, the bound symbol is no longer in the index, or the lookup itself failed.

An empty answer always says which of these produced it, so "no tests" is never ambiguous between "nothing guards this" and "we could not look". That holds in every format: the `table` format prints a `Could not determine` table listing the unresolved links with their reason, `json` carries the states and the unresolved rows, and `list` writes the explanation and the unresolved count to stderr so the piped list on stdout stays clean.

Results are capped per consumer and provider pair so one widely-called helper cannot flood the list; when the cap bites, the command prints how many it dropped and `--format json` carries the exact counts.

Use it three ways:

- **CLI**, `repowise workspace impacted-tests <repo:path>...`, the command above.
- **REST**, `GET /api/workspace/test-impact?repo=<alias>&file=<path>` (repeat `file` for several changed files), with the same fields as `--format json`. Field list: [contract matching reference](../reference/WORKSPACE_CONTRACTS.md#test-impact-over-rest).
- **Web UI**, a provider contract's page (`/workspace/contracts/detail`) ends in a **Tests to run** section: the tests grouped by consumer repo, each marked measured or inferred and saying whether the coverage map, the call graph or the import graph found it, and a **Could not determine** list naming the consumer file and the reason. A consumer contract has no such section, since tests are found on the consumer side.

---

## Architecture Conformance

Workspaces let you declare, in `.repowise-workspace.yaml`, which services are *allowed* to depend on which others, and then continuously check the live system graph against those rules. This is your team's **architecture lint**: the intended architecture, expressed as code, verified on every update.

### Declaring rules

Conformance rules live in a `conformance:` block in the workspace config (no separate file). Each rule has a `source` and a `target` *matcher* and an `allow` flag:

```yaml
repos:
  - path: web
    alias: frontend
    tags: [ui, edge]
  - path: services/db
    alias: db
    tags: [data]

conformance:
  rules:
    # Deny rules (allow defaults to false): the dependency is a violation.
    - source: frontend
      target: db
      description: The UI must call the API, never the database directly.
    - source: "*"
      target: legacy-payments
    # Tag-based: nothing in the "ui" tier may depend on the "data" tier...
    - source: "tag:ui"
      target: "tag:data"
    # ...except migrations, which are explicitly allowed (an exception).
    - source: migrations
      target: db
      allow: true
```

A **matcher** resolves against service nodes in the [system graph](#system-graph):

| Matcher form | Matches |
|--------------|---------|
| `*` | every service |
| `tag:<name>` | every service whose repo declares that tag (see `tags:` on each repo) |
| anything else | a glob over the node id, repo alias, and display name (`frontend`, `api::*`, `*-worker`) |

A rule with `allow: false` (the default) is a **deny** rule: a structural dependency from a matching source to a matching target is a violation. A rule with `allow: true` is an **exception** that whitelists an otherwise-denied edge. Only structural edges (HTTP, gRPC, event, package, db) are evaluated; behavioral co-change is never a dependency.

### Dependency cycles

Independently of any rules, conformance detects **circular dependencies** among services over structural edges (`A -> B -> ... -> A`). A cycle means the services cannot be built, deployed, or reasoned about independently. Cycle detection runs even with zero rules declared, so every workspace gets it.

### Using it

- **CLI**, `repowise workspace check` prints violations and cycles and exits non-zero when any are found, so it gates CI (the architecture lint):

  ```bash
  repowise workspace check                # human-readable report; exit 1 on findings
  repowise workspace check --format json  # raw report JSON (still exits 1 on findings)
  ```

  It recomputes from the persisted system graph, so editing rules and re-running picks them up without a full re-index.
- **REST**, `GET /api/workspace/conformance` returns the report from the most recent update (filterable by `repo`).
- **MCP**, `get_conformance` exposes violations and cycles to an agent. It is opt-in (`mcp.tools: ["+get_conformance"]`, see [MCP_TOOLS.md](../agent/MCP_TOOLS.md#get_conformance)). Without it, the `get_risk` PR-mode directive still gains `conformance_violations` and `dependency_cycles` blocks for the findings the diff's repo participates in.
- **Conformance view**, the web UI's Conformance page renders a **dependency-structure matrix (DSM)**: services on both axes, each filled cell a dependency tinted by transport, with rule violations ringed red and cycle cells amber. Governance panels list the violations and cycles. Violations also badge the offending edges on the [Live System Map](#live-system-map) (toggle **Conformance**), reusing the same additive overlay as the breaking-change guard.

---

## Architecture Metrics

Conformance and the cycle finder answer *per-relationship* questions (is this edge allowed, is this loop a cycle). Architecture metrics give the one *evaluative* read of the whole system: how coupled it is, where its architectural core is, and a single score you can track over time and compare across workspaces. These are the standard MacCormack / Baldwin / Sturtevant architecture-complexity metrics, computed deterministically over the system graph, no LLM. They use **structural edges only** (http / grpc / event / package / db); co-change is excluded.

### What it computes

- **Propagation cost**, the share of *other* services the average service can reach transitively through dependencies (0% = fully decoupled, 100% = everything reaches everything). The headline coupling number; lower is better.
- **Cyclic core**, the largest cyclic group of services (the largest strongly-connected component of the structural graph). Its size and ratio (core / services) describe how much of the system is tangled together.
- **Architecture type**, `core-periphery` when the core spans a meaningful fraction of the system, else `hierarchical`.
- **Per-service role**, each service is classified from its visibility profile:
  - **Core**, in the largest cyclic group (the architectural center).
  - **Shared**, high visibility fan-in, low fan-out: many services depend on it, it depends on few (a widely-used utility/library).
  - **Control**, high fan-out, low fan-in: it depends on many, few depend on it (an orchestrator / entry point).
  - **Peripheral**, lightly coupled in both directions.
- **Architecture score**, a deterministic 1-10 roll-up (matching the Code Health 1-10 convention) from propagation cost, core ratio, dependency-cycle count, and declared-rule violation count. Lower coupling and a smaller core score higher.

### Using it

- **CLI**, `repowise workspace metrics` prints the score, propagation cost, cyclic core, dependency-cycle count, and the per-role service breakdown. CI-friendly plain output; `--format json` emits the raw metrics.

  ```bash
  repowise workspace metrics                # human-readable summary
  repowise workspace metrics --format json  # raw metrics JSON
  ```

- **REST**, `GET /api/workspace/architecture` returns the workspace metrics plus the per-service roles. Computed at request time from the system graph (no separate artifact); the conformance violation count, if a report exists, is folded into the score.
- **MCP**, `get_architecture` gives an agent the score, propagation cost, core members, and role breakdown in one call, the system-structure read to consult before a cross-service refactor. It is opt-in, like `get_conformance` (`mcp.tools: ["+get_architecture"]`).
- **Web**, the **architecture score** appears as a stat on both the Conformance and System Map pages. The DSM header shows score / propagation cost / core size and tints each service's diagonal cell by its role, so the on-diagonal core block stands out. On the Live System Map, toggle **Core** to highlight the cyclic core, and the inspector shows any selected service's role and visibility profile.

---

## MCP Integration

Workspace init registers the MCP server with your editors (skip with `--no-editor-setup`). The MCP server is workspace-aware:

- **Default repo context**, queries go to the primary repo unless you specify otherwise
- **Cross-repo context**, single-repo tools add co-change and contract evidence from the other repos
- **Repo parameter**, most tools accept an optional `repo` parameter to target a specific repo. Four also accept `"all"` to query across the workspace: `get_overview` (the cross-repo topology), `search_codebase` (results from every repo), `get_dead_code` (findings from every repo) and `get_why` with a query (decisions from every repo). The other tools answer about one repo at a time; see [MCP_TOOLS.md](../agent/MCP_TOOLS.md#workspace-mode)
- **Opt-in workspace tools**, off by default and enabled with `mcp.tools` in `.repowise/config.yaml`: `get_blast_radius` ([Cross-Repo Blast Radius](#cross-repo-blast-radius)), `get_conformance` ([Architecture Conformance](#architecture-conformance)) and `get_architecture` ([Architecture Metrics](#architecture-metrics))

---

## File Layout

After workspace init, your directory looks like:

```
my-workspace/
  .repowise-workspace.yaml        # Workspace config (repo list, default, settings)
  .repowise-workspace/            # Shared cross-repo data
    cross_repo_edges.json          # Co-change pairs and package deps
    contracts.json                 # Extracted API contracts and links
    system_graph.json              # Service-granular system graph + diagnostics
    breaking_changes.json          # Breaking provider changes vs the last index
    conformance.json               # Architecture rule violations + dependency cycles
  .claude/
    CLAUDE.md                      # Workspace-level CLAUDE.md for AI editors
  backend/
    .repowise/                     # Per-repo index, same as single-repo mode
  frontend/
    .repowise/
```

What lives in each repo's `.repowise/` is described in
[CONFIG.md](../reference/CONFIG.md#the-repowise-directory).

### What goes in `.gitignore`

Add these to your `.gitignore`:

```gitignore
.repowise/
.repowise-workspace/
.repowise-workspace.yaml
```

The workspace config and data are local: they can reference absolute paths and contain generated analysis that should be rebuilt per-machine.

---

## FAQ

### Can I add repos that live outside the workspace directory?

Yes. Use `repowise workspace add /path/to/external-repo`. The path is stored relative to the workspace root if possible, or as an absolute path otherwise.

### What happens if I run `repowise init` (without `.`) in a workspace?

It runs in single-repo mode for the current directory, ignoring the workspace. Use `repowise init .` from the workspace root to initialize or re-initialize the workspace.

### Can I have nested workspaces?

No. Repowise searches upward for `.repowise-workspace.yaml` and uses the first one it finds. Nested workspace configs are not supported.

### How do I update a workspace after code changes?

```bash
repowise update              # Update the primary repo
repowise update --workspace  # Update all workspace repos
```

Each stale repo picks docs vs index-only the same way a single-repo update does, from its own `docs_enabled` (set at init) plus any override on the command. Repos with docs enabled regenerate their wiki (pages, diagrams, decisions) through the full docs path, so a workspace wiki stays as fresh as one you update repo by repo; the rest just refresh the index. Force docs everywhere with `repowise update --workspace --docs` (each repo needs an LLM provider/key, or pass `--provider`), or keep it index-only with `--no-docs`.

Or use watch mode for automatic updates:

```bash
repowise watch --workspace
```

### How do I re-run just the cross-repo analysis?

Cross-repo analysis runs during `repowise init .` and `repowise update --workspace`. To force a re-run, run `repowise init .` again; it detects existing indexes and only re-runs what is needed.

### Does the MCP server handle multiple repos?

Yes. A single MCP server instance serves all workspace repos. It uses lazy-loading with LRU eviction (max 5 repos loaded simultaneously) to manage memory. The default repo is always kept in memory.

### Can I use `repowise` with git worktrees?

Yes, automatically. Running `repowise init` or `repowise update` inside a linked worktree detects the base checkout, seeds the worktree's index from it, and incrementally updates only the files that differ on your branch. No flags needed; `--seed-from <path>` and `--no-seed` exist as overrides. See [WORKTREES.md](WORKTREES.md).
