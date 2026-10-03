# Workspace contract matching reference

How a [workspace](../scale/WORKSPACES.md) names, reads and matches cross-repo
contracts, for when you need to know why a link did or did not form. Start from
[Extraction Diagnostics](../scale/WORKSPACES.md#extraction-diagnostics), which
names the reason for each unmatched consumer. The support matrix of frameworks
and libraries is in [API Contract Extraction](../scale/WORKSPACES.md#api-contract-extraction).

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
default queue is not recorded, since every app has one. RabbitMQ
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

Data/DB contracts use the id scheme `data::<table>` and render as a `db` edge in the [system graph](../scale/WORKSPACES.md#system-graph). An ORM model with no explicit table name takes its library's default: the Prisma model name, the TypeORM class name in snake case, the Sequelize model name pluralized, the Eloquent class name in snake case and plural. A service that only models a table (an ORM class, no migration) is linked to the service whose migration or DDL defines that table's schema, when exactly one service defines it; two services that each migrate a table of one name are read as separate databases and not linked. The consumer side (SQL string matching) is heuristic and lower-confidence than the ORM-based providers; unlike HTTP and gRPC, there is no field-level breaking-change diffing for data contracts, only table/route-level removal.

## HTTP routes and calls

HTTP routes are matched on their **full** path: a router mount prefix
(`APIRouter(prefix=...)`, `include_router(prefix=...)`, Express `app.use('/x', router)`,
Go route groups, Laravel `Route::prefix(...)->group(...)` and `Route::group(['prefix' => ...])`)
is stitched onto each handler path before matching. Laravel's `routes/api.php` is
served under `/api` unless `bootstrap/app.php` (`apiPrefix`) or a route provider says
otherwise, and `Route::resource` / `apiResource` expand into the routes they register. A NestJS
route is served at the app's `setGlobalPrefix` (unless its `exclude` list names the route), then
its URI version (`enableVersioning`, `@Version`), then the `@Controller` prefix. A call through an
axios, ky, got or ofetch instance is read with the instance's `baseURL` / `prefixUrl`, also when
another file imports the instance. An Angular `HttpClient` call is read on any receiver typed
or injected as `HttpClient`, with `environment.apiUrl` folded from `src/environments/` and class
fields built on it; a base an interceptor prepends is not read. A client call
whose base URL is an unresolved placeholder (`fetch(\`${API_BASE}/users\`)`) matches
on the host-relative path; the link is **exact** when exactly one workspace service
provides that path and a lower-confidence **candidate** when the target is ambiguous.

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
