# REST API Reference

`repowise serve` publishes its own endpoint reference: a machine-readable
OpenAPI document at `/openapi.json` and a Swagger UI at `/docs` (ReDoc at
`/redoc`), generated from the FastAPI route signatures and the Pydantic
response models. Methods, paths, parameters, request/response schemas and
status codes live there and cannot drift, because they *are* the code.

This page carries only what OpenAPI does not: which endpoints make model calls,
how authentication behaves, the shape of an error, how the two SSE streams are
sequenced, and the conventions several routes share. Start at `/docs` for a
specific endpoint; come back here for the things a schema cannot state.

Nothing in this document duplicates a route table on purpose. Where a fact
below is derived from code, the file is named so the claim can be re-checked.
The default API address is `http://127.0.0.1:7337` (`docs/reference/CLI_REFERENCE.md`).

---

## 1. Which endpoints make model calls

Model calls are the operations that spend money against the provider configured
for the repository. This is not visible in the OpenAPI schema, and it is the
first thing an integration needs to know.

Two things decide whether a call happens. A provider must resolve for the repo
(per-repo `.repowise/config.yaml` + `.repowise/.env`, a per-repo UI selection, a
server-global key, or the process environment; see `provider_config.py`), and the
job must be a mode that generates documentation or runs decision extraction
(`job_executor.py`). No provider resolves, no spend: an index/sync job logs
`docs_skip_reason` and continues without documentation, while the generate paths
fail instead of silently doing nothing.

### 1.1 Endpoints that can spend

| Endpoint | What makes the call | Notes |
|---|---|---|
| `POST /api/repos` (`index: true`, the default) | Enqueues a first full index | Same work as `/index` below; the response carries `initial_job_id` |
| `POST /api/repos/{repo_id}/index` | `initial_index` job: full pipeline plus LLM docs | Template wiki with no provider: no model, no cost |
| `POST /api/repos/{repo_id}/sync` | Incremental index, then regenerate only the pages a recent change touched | Model calls on the changed-page subset, not the whole wiki |
| `POST /api/repos/{repo_id}/full-resync` | `full_resync` job: regenerate all documentation | The most expensive endpoint in the API |
| `POST /api/repos/{repo_id}/generate` | Writes an explicit page selection with a model (`repowise generate` over HTTP) | Fails (job `failed`) if no provider resolves |
| `POST /api/pages/lookup/regenerate` | `single_page` job: regenerate one page | Same failure mode |
| `POST /api/repos/{repo_id}/chat/messages` | Up to 10 model turns, plus a model call inside the `get_answer` tool when the model uses it | See the SSE section; each turn bills input tokens again |
| `POST /api/repos/{repo_id}/refactoring/{suggestion_id}/generate-code` | One `provider.generate` for the refactored code and diff | Returns `cached: true` with no call when the same plan content was generated before |
| `POST /api/repos/{repo_id}/preflight` | Provider smoke test: a live `generate(..., max_tokens=50)` | Tiny but real spend, before any index job runs |
| `POST /api/providers/{provider_id}/validate` | Same live smoke test, scoped to one provider | Returns `{ok, provider, model, error}` instead of raising |
| `POST /api/workspace/sync` | Fans the same job machinery out to every workspace repo | `full_resync=true` resyncs each repo; the default is an incremental sync per repo |

Verified against: `packages/server/src/repowise/server/routers/repos.py`
(`sync_repo`, `full_resync`, `generate_pages`, `preflight_index`),
`packages/server/src/repowise/server/routers/pages.py`
(`regenerate_page_by_query`), `packages/server/src/repowise/server/routers/chat.py`
(`chat_messages`), `packages/server/src/repowise/server/routers/refactoring.py`
(`generate_refactoring_code`), `packages/server/src/repowise/server/routers/providers.py`
(`validate_provider`), `packages/server/src/repowise/server/routers/workspace.py`
(`sync_workspace`), `packages/server/src/repowise/server/job_executor.py`
(`execute_job`, `_run_generate_job`, `VALID_JOB_MODES`),
`packages/core/src/repowise/core/analysis/health/refactoring/llm/enrich.py`
(`enrich_suggestion`, `llm_enrichment_enabled`).

The `sync` row is worth stating plainly, because the issue this page came from
described `/sync` as "no LLM calls". That was true when the pipeline ran
`generate_docs=False` and nothing else; it is not true today.
`job_executor.py` runs `_incremental_page_regen` with `llm_client` whenever the
mode is `sync` and a provider resolved, so a sync can bill for the pages a
change touched.

### 1.2 Endpoints that spend without a request

Two paths enqueue sync jobs on their own, and a sync job can spend:

- `POST /api/webhooks/github` and `POST /api/webhooks/gitlab` create a sync job
  for pushes to the registered default branch (`routers/webhooks.py`).
- The scheduler's polling fallback runs every 15 minutes in the serving
  process, and enqueues a sync job for any repo whose git HEAD has moved past
  its stored `last_sync_commit` (`packages/server/src/repowise/server/scheduler.py`).

A `repowise serve` left running with a provider configured, a checkout that
keeps moving, and a default branch that receives pushes can therefore spend
without anyone calling this API. The CLI flag and env to quiet CLI-side
bookkeeping (`--no-cost-tracking` / `REPOWISE_NO_COST_TRACKING`) do not
disable the model calls themselves.

### 1.3 Endpoints that never make model calls

- `POST /api/repos/{repo_id}/dead-code/analyze` runs an `index_only` job;
  `job_executor.py` skips provider resolution entirely for that mode.
- `POST /api/repos/{repo_id}/generate/estimate` resolves the provider (to name
  it and price with the right model) and calls the pricing table only. It is a
  read-only preflight, not a model call.
- `GET /api/repos/{repo_id}/chat/suggestions` goes through the same grounding
  reads as chat but makes no model call.
- `POST /api/repos/{repo_id}/claude-md/generate` renders `CLAUDE.md` from
  indexed data synchronously, with no LLM call (`routers/claude_md.py`).
- `POST /api/repos/{repo_id}/blast-radius` is a deterministic graph analysis
  (`packages/core/src/repowise/core/analysis/pr_blast.py`).
- Every status update, deletion, settings write, and read-only report.

### 1.4 One data-leaving call

`POST /api/feedback` performs no model call but is not local: it forwards the
message to `https://api.repowise.dev/feedback`, tagged `source: "oss"` with the
server version, and answers `502` when that host is unreachable
(`routers/feedback.py`). Nothing else in the API sends request payloads to a
hosted service; the hosted URLs in `provider_config.py` are the providers the
operator configured.

### 1.5 Cost accounting, and an ambiguity worth flagging

`GET /api/repos/{repo_id}/costs` and `/costs/summary` read the `llm_costs`
table. On the server's own pipeline jobs the code passes `cost_tracker=None`
to `run_pipeline` (`job_executor.py`: "the server derives spend from the
generated pages' token counts rather than a per-repo CostTracker, so none is
wired here"), and `LlmCost` rows are only ever constructed by
`core/generation/cost_tracker.py`. So rows reach that table from other writers:
the CLI running against the same repository, and the server-side `get_answer`
synthesis path, which records under operation `answer_synthesis`
(`packages/server/src/repowise/server/mcp_server/tool_answer/synthesis.py`).

The job progress stream's `actual_cost_usd` sums `llm_costs` rows for the
repository with `ts >= job.started_at` (`routers/jobs.py`). It will therefore
report spend recorded in the job's window by those other writers, not a bill
the job computed for itself. The code does not reconcile the two behaviours;
treat the field as "spend recorded while this job ran" and not as the job's own
total. Per-page `input_tokens` / `output_tokens` are persisted on the pages a
generate job wrote, and the job's own totals land in its `config` as
`total_input_tokens` / `total_output_tokens` (`job_executor.py`,
`_run_generate_job`).

---

## 2. Authentication

### 2.1 The bearer key

Set `REPOWISE_API_KEY` before starting the server. Clients then send:

```
Authorization: Bearer <your-key-value>
```

The comparison is constant-time and the prefix must be exactly `Bearer ` with a
single space (`deps.py`, `verify_api_key` / `bearer_is_valid`; verified: a bare
key and a double space are both rejected).

The key is read once, at import (`deps.py` line 26). Changing the environment of
a running server has no effect; restart it. The webhook secrets are the
exception and are read per request (`routers/webhooks.py`), deliberately, so a
setting change is picked up without a restart.

### 2.2 What happens when the header is absent

| Server state | Caller | Result |
|---|---|---|
| `REPOWISE_API_KEY` unset | loopback peer | Request allowed, no credential needed (open local server) |
| `REPOWISE_API_KEY` unset | non-loopback peer | `403` with `{"detail": "Server is reachable from the network but REPOWISE_API_KEY is not set. Set REPOWISE_API_KEY or bind to 127.0.0.1."}` |
| `REPOWISE_API_KEY` set | anyone | `401 {"detail": "Missing API key"}` without the header; `401 {"detail": "Invalid API key"}` with the wrong key |

"Local" is decided from the peer address, not from `REPOWISE_HOST`: a loopback
`request.client.host` is local, an unknown or non-address peer is remote, and
`Forwarded`/`X-Forwarded-For` headers are deliberately not trusted
(`deps.py`, `client_is_local`). The known consequence is that a same-host
reverse proxy makes every peer loopback, so that deployment shape still needs
the key. `repowise serve --host 0.0.0.0` without a key prints a warning at
startup (`packages/cli/src/repowise/cli/commands/serve_cmd.py`) and the
requests themselves are refused as above. All three rows were exercised against
a running app with a fabricated remote peer.

### 2.3 Which endpoints enforce it

Every router under `/api` is protected except the two webhook routes. The two
probe endpoints are unprotected by design:

| Route | Credential |
|---|---|
| `/health`, `/metrics` | none (`routers/health.py`: "these endpoints are NOT protected by API key auth") |
| `/api/webhooks/github` | GitHub's `X-Hub-Signature-256` HMAC-SHA256 header over the raw body, `sha256=` prefix required |
| `/api/webhooks/gitlab` | GitLab's `X-Gitlab-Token` shared secret |
| `GET /api/jobs/{job_id}/stream` | bearer, or the per-job `?token=` described in section 4; this one route cannot carry a router-level bearer dependency because an `EventSource` cannot set headers |
| everything else under `/api` | bearer |

Counting the published document: 172 operations, 168 of which declare the
`APIKeyHeader` security scheme and 4 of which do not, and those 4 are exactly
the table's first three rows (the stream route declares the scheme even though
it also accepts the token, which is a third reason this page exists).

Webhook behaviour when the secret is not configured mirrors the API key:
loopback callers are accepted, a non-loopback caller gets `403`; a bad signature
is `401 {"detail": "Invalid signature"}`, a missing `sha256=` prefix is `401`,
and a bad GitLab token is `401 {"detail": "Invalid token"}` (`routers/webhooks.py`).
Verified by request: remote peer with no key gets the 403 and the exact detail
string.

### 2.4 Two adjacent facts OpenAPI also omits

- CORS is wide open: `allow_origins=["*"]`, `allow_credentials=True`,
  `allow_methods=["*"]`, `allow_headers=["*"]` (`app.py`). Verified by request:
  a preflight from an arbitrary origin is answered `200` with
  `access-control-allow-origin` echoing that origin, and the same header comes
  back on a simple request. Any origin can therefore call the API from a
  browser, which is only safe because the key is what actually guards the data.
- `POST /api/providers/{provider_id}/key` stores a key server-side and, when
  `repo_id` is supplied, also writes it to that repository's `.repowise/.env`
  (`routers/providers.py`, `provider_config.set_api_key`). The API can write a
  secret to disk; treat those two endpoints as privileged.

---

## 3. Error and envelope conventions

### 3.1 Errors are `{"detail": ...}`

Handlers raise `HTTPException`, which FastAPI renders as
`{"detail": <string>}`. The two application-level exception handlers keep that
shape: an uncaught `LookupError` becomes `404 {"detail": str(exc)}` and an
uncaught `ValueError` becomes `400 {"detail": str(exc)}` (`app.py`,
`not_found_handler` / `bad_request_handler`).

Request-validation failures (`422`) are the one different detail shape: a list
of objects with `type`, `loc`, `msg`, and `input`. Verified:

```json
{"detail":[{"type":"missing","loc":["query","repo_id"],"msg":"Field required","input":null}]}
```

An error that reaches Starlette's default handler is the other exception:
`500` with a `text/plain` body of `Internal Server Error`, not JSON. A client
that assumes every non-2xx body parses should handle that case.

Status codes, counted from the source. `HTTPException` raise sites in
`packages/server/src/repowise/server/`: `404` 88 times and `400` 29 times
dominate, then `409` (7), `401` (6), `403` (4), `422` (3), `500` (3), `501` (3,
the three graph routes that require `networkx`), and one `502` (the feedback
relay). The success codes are declared on the routes instead: `202` on the eight
routes that answer with an "accepted" body (the envelope table below), `201` on
repository and decision creation, `204` on the two provider-key writes.

`409` is the one worth knowing before it happens: it usually means "a job is
already in progress for this repository". Every job-launching endpoint is
guarded by one-active-job-per-repo, and the refusal is the same everywhere
(`repos.py`, `dead_code.py`, `jobs.py`, `workspace.py`).

### 3.2 Envelopes

There is no global response envelope. A list endpoint returns a JSON array, a
detail endpoint returns the object, and two conventions sit on top:

| Shape | Where | Meaning |
|---|---|---|
| `{job_id, status: "accepted", stream_token}` with `202` | `POST /api/repos/{id}/sync`, `/full-resync`, `/generate`, `/index`, and `POST /api/pages/lookup/regenerate` | Attach to `/api/jobs/{job_id}/stream` immediately; the token is section 4.1 |
| `{job_id, status: "accepted", repository_id}` with `202` | `POST /api/repos/{id}/dead-code/analyze` | A job, but no stream token is minted here |
| `{status: "generated", path, generated_at}` with `202` | `POST /api/repos/{id}/claude-md/generate` | Synchronous work reported asynchronously; no job exists |
| `{results, accepted, skipped, errors}` with `202` | `POST /api/workspace/sync` | One entry per workspace repo, with its own `job_id` when accepted |
| `RepoResponse` with `201` and `initial_job_id` | `POST /api/repos` with `index: true` | Registration that also enqueued a first index |
| `{ok: true}` (and `{ok: true, deleted_pages: N}` for repo deletion) | mutations with nothing else to report, e.g. conversation delete, repository delete | Acknowledgement only |
| `{event_id, status: "accepted"}` with `200` | both webhooks | The stored event's id, not a job id |
| `X-Repowise-Redirected-From: <requested id>` response header | `GET /api/pages/{page_id}` and `/api/pages/lookup` | A retired page id resolved to its successor; the header names the id that was asked for (`routers/pages.py`) |

Enum vocabularies are shared deliberately and are the same four values wherever
one appears: `open`, `acknowledged`, `resolved`, `false_positive` for health
findings, dead-code findings, refactoring plans and refactoring opportunities
(`ALLOWED_STATUSES`, `routers/code_health/statuses.py`). An unknown value is a
`400`, not a silent no-op.

---

## 4. Streaming semantics

Two endpoints return `text/event-stream`. OpenAPI describes both as
`application/json` with a `200`, which is the clearest single example of what a
schema cannot carry.

Both set `Cache-Control: no-cache, no-transform` and `X-Accel-Buffering: no`;
the chat stream adds `Connection: keep-alive`. The `no-transform` matters when
the API is fronted by the web app's Next.js rewrite, whose compression
middleware would otherwise buffer the whole stream. Verified on the wire by
reading the response headers of both routes.

### 4.1 `GET /api/jobs/{job_id}/stream`

Authentication, in order (`routers/jobs.py`, `authorize_job_stream`): an open
server (no key, loopback peer) is allowed; a valid bearer is allowed; a valid
`?token=` is allowed for that job id only. Otherwise `401` with the JSON body
`{"detail": "Missing or invalid job stream credentials"}` and no stream.

The token is minted at job launch and again on every authenticated job read. It
is an HMAC over the job id, signed with `REPOWISE_API_KEY` (or a per-process
random secret when no key is configured), valid for between one and two hours by
construction: the expiry is quantised to a 3600-second bucket
(`packages/server/src/repowise/server/stream_auth.py`). Minting twice for the
same job inside a bucket returns the identical string, so the browser's
`EventSource` URL does not change on every poll and the client does not
reconnect in a loop. `JobResponse.stream_token` is populated only while the job
is `pending` or `running`; it is `null` once the job is terminal. Verified by
request: two mints matched, a valid token streamed, a tampered token and a token
minted for a different job were both rejected `401`, and a bearer also streamed.

Frames, in the order they are written:

| `event:` | `data:` payload | When |
|---|---|---|
| `message` | `{seq, ts, level, text, phase}` | Pipeline narratives (phase starts, per-file warnings) drained from the in-memory ring buffer, up to 500 entries per job |
| `progress` | `{job_id, status, completed_pages, total_pages, failed_pages, current_level, phase, actual_cost_usd, error_message}` | Once per second, after any pending `message` frames |
| `done` | identical to the final `progress` payload | After the `progress` frame whose `status` is `completed`, `failed`, or `cancelled`; the generator returns immediately after |
| `error` | `{"detail": "Job not found"}` | Job id unknown to every database; the only frame that uses `detail` rather than the progress shape |

How a client knows the stream ended: a terminal `status` produces a `progress`
frame, then a `done` frame carrying the same object, then the connection closes.
A job id that exists in no database produces the single `error` frame and closes
with HTTP `200` (verified: the status line is sent before the body, so a
nonexistent job is not a 404). A `401` is the only pre-stream failure. The
server also stops on client disconnect, checked each loop iteration.

`event: message` is the SSE default event type, so an `EventSource` receives it
on `onmessage`; `progress`, `done`, and `error` need named listeners. The
shipped web client is the reference implementation of this contract
(`packages/web/src/lib/hooks/use-sse.ts`, `use-job.ts`): it re-polls
`GET /api/jobs/{job_id}` every 5 seconds while the job is live, takes the fresh
`stream_token` from that read, and treats `done` as the trigger for a final
refresh.

### 4.2 `POST /api/repos/{repo_id}/chat/messages`

This stream is a POST whose body is `{message, conversation_id?, provider?,
model?, context?}`. It authenticates with the bearer header only; there is no
`?token=` path, because a `fetch` can set headers where an `EventSource` cannot.

Failures that happen before the stream starts are ordinary JSON responses with a
normal status: unknown repo `404 {"detail": "Repository ... not found"}`,
unknown provider override or no resolvable provider `400`/`422`, a provider that
cannot stream `422` (verified: the 404 for an unknown repo, with
`application/json` content type).

Once the stream is open, every frame is the same shape:
`event: data` followed by `data: {...}` whose JSON carries a `type`
discriminator. Note the event *name* is literally `data`, not the SSE default
channel; the shipped web client reads the raw body and switches on `type`, so it
never depends on the event name.

| `type` | Payload | When |
|---|---|---|
| (no frame) | `retry: 3000` | First bytes of the stream, before any event; a reconnection hint, not an event |
| `grounding` | `{tool_id, tool_name, input, summary, artifact}` | Optional: the one page-context read the server makes before the first model turn, so the answer starts grounded |
| `text_delta` | `{text}` | Streaming model text, in order |
| `tool_start` | `{tool_id, tool_name, input}` | The model asked for a tool |
| `tool_result` | `{tool_id, tool_name, summary, artifact}` | That tool returned |
| `truncated` | `{loops}` | Every one of the 10 agentic loops ended in a tool call, so no final answer exists |
| `suggestions` | `{suggestions}` | Follow-up suggestions for the turn that just finished |
| `done` | `{conversation_id, message_id, user_message_id, provider, model}` | Terminal, always the last frame on success |
| `error` | `{message}` | Terminal replacement for `done` (provider error, internal error, unknown conversation) |

Ordering: `grounding` if any, then the loop's own frames, then `suggestions` if
any, then `done`. `error` can occur anywhere after the first frame and ends the
stream there. The contract the client relies on: `done` and `error` are the only
two values that stop the "assistant is typing" state, so no other path may be
the last frame without one of them (`routers/chat.py`, and
`tests/unit/server/test_chat_stream_terminal_event.py` pins exactly that).
`conversation_id` in `done` is how a client learns the id of a conversation the
server created for it.

---

## 5. Conventions that repeat across routes

### 5.1 `fields=` projection, three routes, two vocabularies

`fields` trims a heavy response. The valid values differ per route, so read them
from `/docs`; the shared idea is that the default is the full payload and a
listed summary payload trades bulk for a count.

| Route | Values | Effect |
|---|---|---|
| `GET /api/pages` | `full` (default), `summary` | `summary` drops `content` and `metadata` and adds `content_chars`; on a large wiki that is ~95% of a listing's bytes |
| `GET /api/repos/{repo_id}/files/{file_path}` | `full` (default), `slim` | `slim` drops the four unbounded payloads (wiki body, covered-line array, per-function blame, finding detail maps) |
| `GET /api/repos/{repo_id}/health/files` | `full` (default), `summary` | `summary` omits the per-row keys only the table and drawer read |

The invalid-value behaviour differs too, and is worth knowing when writing
client-side validation: `/api/pages` and `/files/{file_path}` raise
`400 {"detail": "Unknown fields 'x'. Valid: ..."}`, while
`/api/repos/{repo_id}/health/files` validates with a regex and lets FastAPI
raise the `422` list shape. Verified by request against all three.

`GET /api/pages` is also the one route that turns `response_model` off: the two
shapes are serialized by the models themselves, so its OpenAPI `200` schema is
empty. The route still declares only `application/json`.

### 5.2 Code-health `scope=` and `counts=`

Two query parameters repeat across the `code-health` tag, and both change the
numbers rather than the rows:

- `scope=all` (default) or `production`. `production` drops test files, which
  lowers every figure without a defect having been found.
- `counts=everything` (default) or `code_shape`. `code_shape` removes the
  git-derived half of the score, so the figure describes code shape rather than
  what the repository has been through.

Both are pattern-validated (`422` on an unknown value) and both are applied
before filtering, so `total` and the ranking describe the same population the
score does (`routers/code_health/scope.py`, `routers/code_health/counts.py`).

### 5.3 `repo_id` as a query parameter, and synthetic ids

Thirteen operations take `repo_id` in the query string rather than the path
(`/api/pages`, `/api/search`, `/api/symbols`, `/api/meta/version`, ...). In
workspace mode that value is what routes the request to the right per-repo
`wiki.db`; without it, a lookup lands in the default store and a page that
exists in another repo reads as a 404 (`routers/pages.py`, `/lookup`).

`GET /api/repos` can also return synthetic ids of the form `ws:<alias>` for
workspace entries that are not indexed yet. They are stable and prefixed so
client routing cannot collide with real ids; passing one to
`GET /api/search` returns an empty list rather than falling back to the primary
repo (`routers/search.py`).

### 5.4 Pagination

`limit`/`offset` is the convention (39 operations take `limit`, 19 take
`offset`), with a per-route maximum in the schema. The page-shaped responses
that need to tell a client whether more exists return an explicit
`{total, offset, has_more, next_offset}` instead, e.g.
`GET /api/repos/{repo_id}/refactoring/targets/page`. `GET /api/pages` accepts a
`limit` up to 5000, which is the one route where a full listing is a realistic
request.

### 5.5 Background jobs, one per repository

Six job modes exist (`job_executor.py`, `VALID_JOB_MODES`): `sync`,
`full_resync`, `initial_index`, `index_only`, `generate`, `single_page`. They
are not addressable by name over HTTP; each is the engine behind the endpoints
in section 1. The mode is visible in `GET /api/jobs/{id}` under `config.mode`.

One job per repository may be `pending` or `running` at a time. A second launch
is `409`. On startup, the server marks any job left `running` or `pending` by a
previous process as `failed`, with the message `Server restarted — job
interrupted` on the primary database and `Server restarted; job interrupted` in
workspace repo databases, so a crashed server does not lock a repo out of new
syncs (`app.py`, `reset_workspace_stale_jobs`).

### 5.6 Routes that need the working tree on the serving host

`repowise serve` is normally pointed at a checkout, and several routes read or
write it directly. They are the operations that behave differently on a hosted
deployment with no local checkout:

- write to `.repowise/config.yaml`: `PUT /api/repos/{id}/refactoring/settings`,
  `PUT /api/repos/{id}/decisions/settings` (the latter takes an `etag` from a
  previous read and answers `409` on a lost write race)
- write a file into the repo: `POST /api/repos/{id}/claude-md/generate` (`422`
  when the path is not accessible)
- read the checkout: `GET /api/repos/{id}/file-content` (only files the indexer
  recorded are servable, and `.git`/`.repowise` paths are refused),
  `POST /api/repos/{id}/preflight`,
  `POST /api/repos/{id}/refactoring/{suggestion_id}/generate-code`
  (`404` without an accessible checkout, `403` when `refactoring.llm.enabled`
  is false)
- validate the path at registration: `POST /api/repos` rejects a `local_path`
  that is not an existing git directory with a `422` body

### 5.7 Response types OpenAPI gets wrong

Verified by reading the routes and, where noted, by request:

| Route | OpenAPI `200` says | The route actually sends |
|---|---|---|
| `GET /api/repos/{id}/health/badge.svg` | `application/json` | `image/svg+xml` (checked on the wire), `Cache-Control: max-age=300, public` |
| `GET /api/repos/{id}/export` | `application/json` | `application/zip` with `Content-Disposition: attachment` |
| `GET /api/repos/{id}/file-content` | `application/json` | `text/plain` (`PlainTextResponse`) |
| `GET /api/jobs/{id}/stream` | `application/json` | `text/event-stream` |
| `POST /api/repos/{id}/chat/messages` | `application/json` | `text/event-stream` (JSON only for pre-stream failures) |
| `GET /api/graph/{id}/c4/mermaid`, `/structurizr` | `text/plain` | `text/plain` (correct) |

---

## 6. Orientation: which part of `/docs` to open

Swagger groups operations by tag. There are 33 tags in the published document,
one per router area; this table maps an area to the tag to open there.

| Area | OpenAPI tag | Router |
|---|---|---|
| Repository registration, sync, generate, export, file content | `repos` | `routers/repos.py` |
| Job status, cancel, SSE progress | `jobs` | `routers/jobs.py` |
| Wiki pages, versions, notes, regenerate | `pages` | `routers/pages.py` |
| Hybrid search over pages | `search` | `routers/search.py` |
| Symbol index and call graph | `symbols` | `routers/symbols.py` |
| Dependency graph, communities, paths, ego graphs | `graph` | `routers/graph/` |
| C4 diagrams, Structurizr and Mermaid export, zoom map | `c4` | `routers/c4.py` |
| Health scores, findings, coverage, trends, badges | `code-health` | `routers/code_health/` |
| Refactoring plans, opportunities, settings, code generation | `refactoring` | `routers/refactoring.py` |
| Dead-code findings | `dead-code` | `routers/dead_code.py` |
| Change risk, hotspots, ownership, commits, co-changes | `git` | `routers/git.py` |
| Structural coupling report | `coupling` | `routers/coupling.py` |
| Blast radius for a change | `blast-radius` | `routers/blast_radius.py` |
| Architectural decisions, lanes, lineage, policy settings | `decisions` | `routers/decisions.py` |
| Mined session episodes | `episodes` | `routers/episodes.py` |
| Security pattern scan | `security` | `routers/security.py` |
| Cross-repo workspace intelligence | `workspace` | `routers/workspace.py` |
| Codebase chat and conversations | `chat` | `routers/chat.py` |
| Provider list, active selection, keys, validation | `providers` | `routers/providers.py` |
| MCP tool surface per repo | `mcp` | `routers/mcp.py` |
| Version freshness and changelog | `meta` | `routers/meta.py` |
| Cost totals and distill savings | `costs` | `routers/costs.py` |
| Ownership rollups | `owners` | `routers/owners.py` |
| Module health | `modules` | `routers/modules.py` |
| Dashboard overview payload | `overview` | `routers/overview.py` |
| Repository stats and highlights | `stats` | `routers/stats.py` |
| Per-file listing and detail | `files` | `routers/files.py` |
| Knowledge map | `knowledge-map` | `routers/knowledge_map.py` |
| External systems (third-party packages) | `external-systems` | `routers/external_systems.py` |
| `CLAUDE.md` / `AGENTS.md` generation | `claude-md` | `routers/claude_md.py` |
| GitHub and GitLab webhooks | `webhooks` | `routers/webhooks.py` |
| Liveness, readiness, Prometheus metrics | `health` | `routers/health.py` |
| In-app feedback relay | `feedback` | `routers/feedback.py` |

Two rows need a caveat, both verified against the published document. The
`stats` tag holds only `GET /api/repos/{repo_id}/stats/highlights`; the main
`GET /api/repos/{repo_id}/stats` route is tagged `repos`. And
`GET /api/repos/{repo_id}/health/coordinator` carries the `health` tag twice,
so it is listed under `health` alongside `/health` and `/metrics`, which are
the two unauthenticated probes.

---

## How this document was verified

Every claim above was checked against the code in
`packages/server/src/repowise/server/` and, for the pipeline and provider
behaviour, `packages/core/src/repowise/core/`. The counts and the
"which operations declare auth" table come from generating the app's own
OpenAPI document in this checkout (160 paths, 172 operations, 33 tags, 168
operations declaring the security scheme). The runtime behaviours quoted as
"verified by request" (auth outcomes and detail strings, error and 422 shapes,
CORS headers, the 500 body, the SSE frame order and headers, the stream token's
stability and cross-job rejection, the projection routes' status codes) were
executed against a `create_app()` instance with an ASGI transport.

Where the code is genuinely ambiguous, the section says so rather than picking
an answer: section 1.5 on cost accounting is the main one. If you change a route
named here, change this page with it.
