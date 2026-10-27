# Workspace contract matching reference

How a [workspace](../scale/WORKSPACES.md) names, reads and matches cross-repo
contracts, for when you need to know why a link did or did not form. Start from
[Extraction Diagnostics](../scale/WORKSPACES.md#extraction-diagnostics), which
names the reason for each unmatched consumer. The support matrix of frameworks
and libraries is in [API Contract Extraction](../scale/WORKSPACES.md#api-contract-extraction).
The pipeline behind it, and how to add a dialect or a service-name source, is in
[architecture/workspace.md](../architecture/workspace.md).

## How a contract and a link were made

Every contract carries an `origin` and every link a `strategy`, each from a closed
vocabulary with exactly one confidence per value, the rule
[GRAPH.md](../layers/GRAPH.md#every-edge-says-how-it-got-there) applies to call edges. A
link's confidence is its weaker end's times its strategy's factor, and its `match_type`
follows from the strategy.

| Confidence | Contract `origin` | What was established |
|:---:|---|---|
| 0.90 | `idl` | An OpenAPI or proto document declares the operation |
| 0.90 | `package_export` | A published package exports the symbol another repo imports |
| 0.85 | `route_registration` | A framework registers the route, handler, server or hub in code |
| 0.85 | `typed_client` | The parsed index types the receiver as an HTTP client |
| 0.85 | `ddl` | SQL DDL or a migration creates or alters the table |
| 0.85 | `explicit_table` | An ORM model names its table explicitly |
| 0.75 | `client_call` | A known client, broker, socket or query-builder call names the destination |
| 0.75 | `instance_base` | A call on a client instance whose base URL the file reads |
| 0.70 | `dataflow` | The destination was traced across statements to the call |
| 0.70 | `sql_string` | A table named inside SQL text in a string |
| 0.65 | `name_heuristic` | Only a callee or receiver name says it is a client call |
| 0.60 | `name_convention` | A naming convention: an ORM's default table name, a unique short type name |

| Factor | Link `strategy` | `match_type` | What joined the two ends |
|:---:|---|---|---|
| 1.0 | `exact_id` | exact | Both name the same normalized id, method and service wildcards included |
| 1.0 | `pattern_matched` | exact | The consumer's subscription pattern matches the provider's name |
| 1.0 | `binding_routed` | exact | A queue bound to the provider's exchange, with a routing key it receives |
| 1.0 | `shared_table` | exact | A service that only models a table, to the one service defining its schema |
| 1.0 | `service_mapped` | exact | The consumer's host or base token maps to the providing service |
| 1.0 | `unique_path` | exact | The consumer's base is unread and one service serves the path |
| 0.6 | `ambiguous_path` | candidate | The base is unread and several services of one repo serve the path; linked to each |
| 0.6 | `prefix_stripped` | candidate | Matched only after stripping mount, version or base prefixes |
| 0.5 | `host_only` | candidate | The host names a workspace service, but no route there serves the path; a service-level edge |
| 1.0 | `gateway_route` | exact | A gateway's config forwards the path to the service: the gateway's own edge, and a call through it linked on the forwarded path |
| 1.0 | `manual` | manual | Declared in the workspace config |

A new dialect picks an existing origin, not a number. A system-graph edge carries the
strategy of the link that gave it its confidence, and `manifest` for a package dependency.

## Sockets

An endpoint is identified by its path (`socket::/hubs/game`); a message by its
event within a scope, `socket::<scope>#<event>`, where the scope is a socket.io
namespace (`/` unless the file names one) or a broadcast channel. Channels keep
the wire prefix Pusher gives them (`private-orders.{param}`), and Echo's event
names are read the way Echo formats them, so `.listen('OrderShipped')` meets a
Laravel event class `App\Events\OrderShipped` and `.listen('.order.shipped')`
meets `broadcastAs()` returning `order.shipped`. The emitting side is the
provider. socket.io, amqplib's `publish`/`consume`, BullMQ, Redis and NestJS
calls are read only in a file importing the library, since `emit`, `on`,
`publish` and `subscribe` are common method names.

## Topics and queues

A topic, queue or exchange name is read the way a URL is: a literal, or a name
the same file assigns exactly once to a literal (Python, Go, Java, PHP class
constants, and JS/TS `const` including object and enum members such as
`QUEUES.ticketSold`), is resolved; a name built at runtime is skipped, not
guessed. An SQS queue URL is named by its last path segment and an SNS
topic ARN by its last field. A subscription by pattern (NATS `orders.*` /
`orders.>`, Redis `psubscribe`, Kafka `topicPattern`) links to every publisher
whose name it matches. A Laravel job dispatched without a queue runs on the
queue its class declares (`public $queue`, `$this->onQueue()`, `viaQueue()`),
found by the class's fully qualified name; one left on the connection's
default queue is not recorded, since every app has one. A Laravel job class
links only to a Laravel dispatch of that class: a raw publish (amqplib
`sendToQueue`) on a queue of the same name is not the job envelope its worker
reads. RabbitMQ
publishers name an exchange and a routing key while consumers name a queue, so
a queue binding found anywhere in the workspace connects the two: each consumer
of the bound queue links to the exchange's publishers whose routing key the
binding pattern accepts (`*` and `#` follow topic-exchange rules; an empty key
or pattern matches everything, while a key the source does not settle matches
nothing). Such a link carries the exchange as its `contract_id` and the queue as
`consumer_contract_id`. When no consumer of the bound queue is found, the
binding site itself is linked. A publish to the default exchange
(`publish('', 'jobs')`) is a publish to the queue `jobs`.

## Data contracts

Data/DB contracts use the id scheme `data::<table>` and render as a `db` edge in the [system graph](../scale/WORKSPACES.md#system-graph). An ORM model with no explicit table name takes its library's default: the Prisma model name, the TypeORM class name in snake case, the Sequelize model name pluralized, the Eloquent class name in snake case and plural. A service that only models a table (an ORM class, no migration) is linked to the service whose migration or DDL defines that table's schema, when exactly one service defines it; two services that each migrate a table of one name are read as separate databases and not linked. A table on an embedded database (SQLite) links only within its own repo, on either side: a reader or declaration in a file that opens one, and every table in a repo whose code names an embedded engine and no server database (Postgres, MySQL, SQL Server), since that file is the repo's own database. The consumer side (SQL string matching) is heuristic and lower-confidence than the ORM-based providers; unlike HTTP and gRPC, there is no field-level breaking-change diffing for data contracts, only table/route-level removal.

## HTTP routes and calls

HTTP routes are matched on their **full** path: a router mount prefix
(`APIRouter(prefix=...)`, `include_router(prefix=...)`, Express `app.use('/x', router)`,
Go route groups, Laravel `Route::prefix(...)->group(...)` and `Route::group(['prefix' => ...])`,
Fastify `register(plugin, {prefix})`, Koa `new Router({prefix})` and nested `router.use()`,
hapi `register({plugin, routes: {prefix}})`) is stitched onto each handler path before matching.
A Fastify, Koa or hapi prefix that does not resolve to a string, or a plugin mounted at two
different prefixes, leaves those routes out rather than at a guessed path. Laravel's `routes/api.php` is
served under `/api` unless `bootstrap/app.php` (`apiPrefix`) or a route provider says
otherwise, and `Route::resource` / `apiResource` expand into the routes they register. A NestJS
route is served at the app's `setGlobalPrefix` (unless its `exclude` list names the route), then
its URI version (`enableVersioning`, `@Version`), then the `@Controller` prefix. A call through an
axios, ky, got or ofetch instance is read with the instance's `baseURL` / `prefixUrl`, also when
another file imports the instance. An Angular `HttpClient` call is read on any receiver typed
or injected as `HttpClient`, with `environment.apiUrl` folded from `src/environments/` and class
fields built on it; a base an interceptor prepends is not read. A Node `request` call reads a URL
or an options object held in a local (`var options = { uri, method }`), and a base from a
`module.exports = { ... }` module it requires (`endpoints.cartsUrl`). A C# `HttpClient` call takes
the `BaseAddress` its typed (`AddHttpClient<T>`), named (`AddHttpClient("name")` with
`CreateClient("name")`) or Refit (`AddRefitClient<T>`, `RestService.For<T>`) registration sets in
the same repo; a type registered twice with different or unreadable bases takes none. A call to a
method whose body sends its string parameter through `HttpClient` is read with the URL that body
builds. A client call
whose base URL is an unresolved placeholder (`fetch(\`${API_BASE}/users\`)`) matches
on the host-relative path; the link is **exact** when exactly one workspace service
provides that path (`unique_path`) and a lower-confidence **candidate** when several
services of one repo serve it (`ambiguous_path`). A call whose route two or more repos
serve, with no host or base naming one, links to none of them and is reported as
`ambiguous_provider`. A generic operational path (`/health`, `/healthz`, `/ready`,
`/metrics`, `/status`, `/version`, `/actuator/info`, the root) names no service, so it links
only to the repo its host or base resolves to or, with neither, to a server in the caller's
own repo; it never links to another repo by path alone.

## Service names

A consumer that names its target (`http://catalogue:8080/...`, `${CATALOGUE_URL}/...`) is
resolved to a repo in this order: a `contracts.service_bases` entry, a repo alias (also as
the leading label of `<name>.svc.cluster.local`), then the service-name map the workspace
reads from its own config. A host the map places links with strategy `service_mapped`; when
no route in that repo serves the path, the call becomes a `host_only` service-level edge in
the system graph (kept in `service_links`, apart from route links). The consumer's `meta`
records `target_via` (the source) and `target_evidence` (the file).

| Source | Files read | Name | Repo |
|---|---|---|---|
| `aspire_apphost` | `AppHost.cs`, `Program.cs` holding `DistributedApplication.CreateBuilder` | `AddProject("catalog-api", "../Catalog.API")`, or `AddProject<Projects.X>(...)` through the AppHost's `<ProjectReference>` | The project's directory |
| `compose` | `docker-compose*.yml`, `compose*.yml` | Service name, `container_name`, `hostname`; `environment` keys whose value is an address. `${VAR-default}` and `${VAR:-default}` read as the default | `build` context (or its Dockerfile's directory), else an image basename equal to a repo alias. A missing or empty context (an uninitialised submodule) counts as no build |
| `k8s_service` | `*.yaml` under `k8s/`, `deploy/`, `manifests/`, ... | `kind: Service` `metadata.name` | A selector or label value equal to a repo alias, else the manifest's repo when it declares no other Service |
| `spring_application_name` | `application` / `bootstrap` `.yml` or `.properties` | `spring.application.name` | The module the file sits in |
| `spring_base_path` | `.yml`, `.yaml`, `.properties` under `resources/`, `config/`, `configs/` or `shared/` | `server.servlet.context-path`, `server.context-path`, `spring.webflux.base-path`: a base path, not a name | A module's own `src/main/resources/application` or `bootstrap` file (not a profile file): that module. A config server's `<app>.yml`: the service named `<app>` |
| `spring_gateway` | `application` / `bootstrap` in a module's resources, or a config-server file named `*gateway*`, `*zuul*`, `*proxy*`, `*edge*` (`.yml` or `.properties`) holding `routes` | Spring Cloud Gateway `routes[]` (`lb://name`, `http://host`) and Zuul `zuul.routes` | A gateway route (below) |
| `dockerfile_env` | `Dockerfile`, `Dockerfile.*`, `*.Dockerfile` holding `ENV` | `ENV` keys whose value is an address (`CATALOGUE_HOST=catalogue`) | The host the value names |
| `env_file` | `.env`, `.env.*`, `*.env` (what a compose `env_file:` names) | Keys whose value is an address; no other value is kept | The host the value names |
| `nginx` | `*.conf`, `*.conf.template`, `*.conf.tmpl` holding `proxy_pass` | Prefix `location` blocks with `proxy_pass` to a host, an `upstream` of one host, or an envsubst `${X_HOST}` in a template | A gateway route (below) |
| `ocelot` | `ocelot*.json`, `configuration.json` holding `UpstreamPathTemplate` | Ocelot `Routes` (or `ReRoutes`): `ServiceName`, or the one host in `DownstreamHostAndPorts` | A gateway route (below) |
| `yarp` | `appsettings*.json`, `yarp*.json` holding `ReverseProxy` | YARP `Routes` and the one host their cluster's `Destinations` name | A gateway route (below) |
| `yarp_forwarder` | `Program.cs`, `Startup.cs` holding `MapForwarder` | The destination host of `MapForwarder(pattern, destination[, template])` | A gateway route (below) |

A base path prefixes every Spring route of its module before matching, so `@PostMapping("/users")`
under `context-path: /uaa` is `POST /uaa/users`. A module given two different base paths gets
none.

Each repo is walked once per update for these file names; test, example and build-output
directories are skipped, and so is any file the code-origin classifier calls test, docs or
example, vendored or generated (a compose file under `docs/`, `demo/` or `e2e-tests/`).
Files at the workspace root are read too. A name two files place in different repos is left
unmapped rather than guessed. To override or fill a gap, add the host or base token to
`service_bases`, which always wins.

A gateway source states routes rather than names: a path prefix the gateway forwards to a
service, and the path it forwards (Spring `StripPrefix`, `PrefixPath` and a
`RewritePath=/x/?(?<s>.*), /y/${s}`; Zuul `stripPrefix`). An nginx `proxy_pass` with a path replaces the location prefix with that path; one
without passes the request path unchanged. The nginx gateway is the repo holding the file. The gateway is the repo holding
an `application` file in its module's own resources, or for a config server's `gateway.yml`
the app its name spells. Each route is a `gateway_route` edge from the gateway to the
service. A route whose forwarded path or target is not read (a Host or Method predicate,
`SetPath`, a service outside the workspace, a prefix sent two ways) rewrites no call but keeps
a shorter prefix from claiming the paths under it. A call that reaches the gateway (its
host names the gateway, or a relative call from the gateway's own pages) is rewritten by the
longest matching prefix and linked to the route's service on the forwarded path; when no
route there serves it, it becomes a `host_only` edge to that service.
Ocelot, YARP `PathPattern` and `MapForwarder` templates rewrite the prefix when both
templates end in the same variables (`/catalog-api/{everything}` to `/{everything}`); any
other template is a blocker. Blockers alone at one prefix each keep their edge.

## Contracts over REST

`GET /api/workspace/contracts` lists contracts and links,
filterable by `contract_type`, `repo` and `role`. Each contract carries its
line, its ingestion symbol id and the extractor's `meta`; each link carries both
symbol ids and both service boundaries. The request/response `schema` is not on
the list, because it runs to full inline type declarations and only one is ever
needed at a time: fetch it with
`GET /api/workspace/contracts/detail?repo=<alias>&file=<path>&id=<contract-id>`,
which returns that one contract with its schema, its links, and its unmatched
reason. All three parameters are required, since a contract id alone is not
unique across repos.

## Package matching

npm and composer dependencies match in two ways: a local path (`file:` specs,
workspace globs, composer `path` repositories) that resolves into a sibling repo,
or a package name that exactly one sibling repo publishes. For npm, a repo
publishes its root `package.json` and its declared workspace members, so a
vendored or fixture `package.json` never claims a name. For composer, every
`composer.json` up to three directories deep counts, outside `vendor/`, hidden
directories, tests, fixtures and examples, which covers split packages such as
`src/Illuminate/Support/composer.json`. A name two repos publish links to
neither.

Python dependencies match the same way. A repo publishes the distribution its root
`pyproject.toml` names and those of its `[tool.uv.workspace]` members. A dependency
is read from each of those projects' `pyproject.toml` (`[project]` dependencies and
optional dependencies, Poetry dependencies and groups) and the `requirements*.txt`
beside it, and names compare in their PEP 503 spelling (`Acme.Core` is `acme-core`).
A Poetry path dependency or a `[tool.uv.sources]` path source resolves by its path
alone. Lock files and `setup.cfg` are not read.

Maven matching is filesystem-only and coordinate-based. Repowise resolves local
reactor modules, local parents, properties, and dependency-management versions,
then links an active direct compile/runtime dependency only when exactly one
selected workspace project publishes that `groupId:artifactId`. Test, provided,
system, optional, profile-only, ambiguous, and external dependencies do not create
production package edges. Bounded diagnostics retain the reason for Maven
non-matches. When a repository has a root `pom.xml`, only that declared reactor is
eligible; unrelated nested example or fixture POMs are not treated as producers.

This does **not** execute Maven, read user settings, download artifacts, resolve
plugins/transitive dependencies/imported BOMs, or infer generated sources. A Maven
package edge is also not a published symbol-level code API or a runnable Maven
target recommendation; those capabilities are reported separately and remain
unsupported.

## OpenAPI comparison boundary

The [breaking-change guard](../scale/WORKSPACES.md#breaking-change-guard) diffs
OpenAPI schemas within this boundary.

OpenAPI comparison covers the common supported subset of `3.0.x`, `3.1.x`, and `3.2.x`: JSON/YAML documents; path/query/header/cookie parameters using OpenAPI's default `style`, `explode`, and `allowReserved` behavior; one `application/json` request body; exactly one explicit JSON 2xx response; recursive objects and arrays; exact primitive types; requiredness; normalized nullability; finite homogeneous scalar enums; and bounded same-document JSON Pointer references. Request rules describe values the provider accepts; response rules describe values consumers may receive, so requiredness, nullability, enum values, and constrained/unconstrained enum transitions reverse between the two sides. Operation removal remains the transport-neutral contract rule.

Only complete sides with the same schema source and comparison-fidelity key are field-diffed. Unsupported/unresolved nodes, extraction-strategy changes, and response-selection changes become warning-level uncertainty, never shortened schemas or false removals. Remote/cross-file references, composition, additional-properties semantics, arbitrary JSON Schema constraints, non-JSON or ambiguous media, multiple materially different success responses, and OpenAPI 2.0 are outside this boundary. No network dereferencing occurs.

## Test impact over REST

`GET /api/workspace/test-impact?repo=<alias>&file=<path>`. Repeat `file` for several changed files in the same repo. The response carries the same fields the `--format json` output has: `recommendations` (each with its consumer repo, the `consumer_files` and bound `consumer_symbol_ids` that reached the test, `basis`, `via`, `confidence` and the contract ids that produced it), `unresolved` with a reason per link, `files_analyzed` with the state each landed on, and the `summary` counts. Over the API an empty answer's `summary.reason` is `no_contract_data`, `no_matching_links` or `lookup_failed`.
