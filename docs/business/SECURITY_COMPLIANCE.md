# Security & Compliance

Written for the person who has to sign off before Repowise touches source code.
It covers what Repowise processes, what it persists, what leaves the machine
under each deployment mode, and how to reduce that to zero.

Short version: Repowise is self-hosted by default. Your source code is read from
disk, parsed in memory, and never persisted. The only content that leaves your
infrastructure is what you send to your own LLM provider under your own key, and
even that is optional. Anonymous CLI telemetry is the one outbound channel we
add, it contains no code, paths, or repo names, and it has three independent
off switches.

- Open-source feature set: [README](../../README.md) · [docs/](..)
- Commercial layer, on-prem topology, licensing: [COMMERCIAL.md](COMMERCIAL.md)
- Telemetry field-by-field: [docs/reference/TELEMETRY.md](../reference/TELEMETRY.md)
- Security-specific contact: [security@repowise.dev](mailto:security@repowise.dev)

---

## 1. Data processed vs data persisted

This is the distinction most security reviews turn on. Repowise reads a lot more
than it keeps.

| Artifact | Processed | Persisted | Where |
|---|---|---|---|
| Raw source code | Yes, read from disk and parsed into an AST in memory | **No** | transient only |
| Dependency graph (NetworkX) | Yes | Yes | local index (`.repowise/`) |
| Embeddings (LanceDB) | Yes | Yes, as non-reversible vectors | local index |
| Generated wiki pages | Yes | Yes | local index |
| Git metadata (commits, authors, churn, co-change) | Yes | Yes | local index |
| Architectural decision records | Yes | Yes | local index |
| Code-health scores and findings | Yes | Yes | local index |

Raw source is processed transiently and never persisted. What survives an index
is a structural and statistical description of the codebase plus the
documentation generated from it.

**On embeddings.** LanceDB stores float vectors produced by the configured
embedding model. They are not encrypted source and they are not reversible back
into the original text. They are, however, derived from your code, so treat the
index directory with the same access controls as the repository itself.

**Where the index lives.** In the OSS distribution, everything above sits in the
`.repowise/` directory inside the repository, on the machine that ran
`repowise init`. Nothing is uploaded. A commercial on-prem install swaps the
local stores for Postgres (metadata) and LanceDB or pgvector (embeddings) inside
your own network, as described in [COMMERCIAL.md §6](COMMERCIAL.md#6-on-premise-deployment).

---

## 2. LLM usage, BYOK, and zero retention

Repowise splits cleanly into layers that need an LLM and layers that do not.

**Zero-LLM layers.** Graph, git, dead code, code health, change risk, and the
security pattern scan are pure local computation over tree-sitter and git data.
`repowise init --index-only` builds all of them with **no outbound LLM calls at
all**. The health pass in particular is deterministic Python: no model, no
network.

**LLM layers.** Wiki/documentation generation, chat, decision extraction, and
the opt-in refactoring code-gen call a model. These are the only paths that can
send code-derived content off the machine.

**BYOK.** You bring your own key: Anthropic, OpenAI, Azure OpenAI in your own
tenant, or a local runtime. Calls go directly from your infrastructure to the
provider you configured. We never see your LLM traffic, and we never proxy it.
Zero data retention applies through your provider's own policy (Anthropic's API
zero-retention terms, your Azure tenant's data-handling terms, and so on), which
means the retention question is settled by a contract you already hold rather
than by us.

**Per-repository provider choice.** The provider is configured per repo, so a
sensitive repository can run fully offline while a lower-sensitivity tooling
repo uses a hosted model.

**Fully offline.** Ollama plus a local embedding model means **zero external API
calls**: no LLM provider, no embedding provider, nothing outbound from the
indexing path. Combined with telemetry disabled (see §3), Repowise makes no
network calls whatsoever.

---

## 3. Telemetry

The CLI reports anonymous, opt-out usage telemetry so we can prioritize work. It
is documented field by field in
[docs/reference/TELEMETRY.md](../reference/TELEMETRY.md).

**What is collected:** an anonymous envelope (`anon_id`, `session_id`,
`cli_version`, `os`, `arch`, `python_version` at major.minor only, `is_ci`),
plus per-event properties: the command name, a known subcommand name, flag
**names** only, a status enum, an exception **class name**, and a duration. For
`init` / `update`, coarse buckets describe the shape of the run (file-count
bucket, top language, docs mode on/off, provider and model, embedder, pages
bucket). MCP tool calls report the tool name, status, duration, and coarse
enums (confidence, retrieval quality, results bucket, index-behind flag).

**What is never collected:** source code or file contents, file paths or
directory names, repository names, package names, symbol or function names,
generated documentation text, flag values, environment-variable values, API keys
or credentials, error messages or stack traces, IP addresses, usernames,
hostnames, email, or anything personally identifiable.

**`anon_id` is a random UUID** stored in `~/.repowise/platform.json`. It is not
derived from a hostname, username, or machine identifier, so it cannot be
reversed to a person. Delete the file and a new, unrelated id is generated.

**Verify before you trust us.** Print the exact payload without sending it:

```bash
REPOWISE_TELEMETRY_DEBUG=1 repowise status
```

**Three independent ways to disable it**, any one of which is sufficient:

```bash
repowise telemetry disable        # persisted to ~/.repowise/platform.json
export REPOWISE_TELEMETRY_DISABLED=1
export DO_NOT_TRACK=1             # the cross-tool standard
```

Running fully offline (§2) also means nothing is sent. Check state with
`repowise telemetry status`.

**Retention:** events are kept for 90 days, then deleted. Only aggregate figures
are used or shared.

Note that [COMMERCIAL.md §2](COMMERCIAL.md#2-what-ships-in-open-source-agpl-30)
summarizes this as "zero telemetry". The precise statement is the one here and
in the telemetry reference: anonymous, opt-out, no code or identifiers, three
off switches.

---

## 4. Self-hosted vs hosted: where the boundary sits

| | Self-hosted (OSS, `pip install repowise`) | Self-hosted commercial (on-prem) | Hosted platform |
|---|---|---|---|
| Where source is read | your machine | your VPC | the hosted indexer |
| Where the index lives | `.repowise/` on your machine | Postgres + LanceDB/pgvector in your network | Repowise-operated infrastructure |
| Who calls the LLM | you, with your key | you, with your key | the platform, or your key |
| Outbound from your network | anonymous telemetry (disableable), your LLM provider | same, plus configured integrations | n/a, you are sending code to the platform |
| Commercial security features (CVE triage, SBOM/VEX, secret history scan, compliance reports, audit trail) | not included | per [COMMERCIAL.md §5](COMMERCIAL.md#5-commercial-capabilities--in-detail) | GA today |

The honest framing: **several commercial security capabilities are GA on the
hosted platform first.** If your requirement is on-prem *and* CVE-aware
dependency analysis, SBOM/VEX, or compliance reporting, that sequencing is a
scoping conversation, not a checkbox. The status of each capability (GA / in
development / planned) is listed unvarnished in
[COMMERCIAL.md §4](COMMERCIAL.md#4-commercial-capabilities--at-a-glance).

**Secrets handling on the hosted secret-detection feature:** only a fingerprint
and a redacted preview are stored, never the secret value.

---

## 5. On-prem and air-gapped topology

A self-hosted commercial install is a set of containers on your infrastructure.
The reference topology is Kubernetes; the same containers run on Nomad or plain
Docker. The OSS Docker images are documented in
[docker/README.md](../../docker/README.md): a full-stack image (API plus Web UI)
and a lean MCP-only image that speaks JSON-RPC over stdio, carries no Web UI, and
exposes no ports.

Components:

1. **API server** (FastAPI), **indexer workers**, and **dashboard** (Next.js).
2. **Postgres** for metadata, **LanceDB** (or pgvector) for embeddings, and an
   in-memory NetworkX graph.
3. **Optional Ollama container** for fully-offline LLM use.
4. **Webhook receivers** for GitHub Enterprise, GitLab self-managed, or Azure
   DevOps, configured per tracked repository.
5. **SSO** through your existing Entra ID / Okta tenant, and **outbound
   integrations** configured with signed service tokens held in the Repowise
   secret store. Availability per [COMMERCIAL.md §5.2](COMMERCIAL.md#52-workflow-integrations-rolling-out).

**Air-gapped.** All services run inside your VPC or air-gapped network. The LLM
provider and the git server sit inside the same boundary. **No outbound
connectivity is required in air-gapped mode.** A packaged air-gapped install
bundle (bundled grammars, embedding model, optional Ollama runtime) is on the
roadmap and marked *planned* in COMMERCIAL.md; today an air-gapped install is
assembled from the containers above rather than from a single bundle.

**Indexing cost profile, for capacity planning.** Graph, git, dead-code, and
code-health layers build in minutes with zero LLM calls
(`repowise init --index-only`). One-time wiki generation scales with repo size
and can run in the background. Incremental updates after each commit complete in
under 30 seconds.

---

## 6. Compliance posture

**Repowise holds no security certifications.** We do not claim SOC 2, ISO 27001,
or any other audited attestation, and you should not accept a vendor answer that
implies otherwise without a report to read.

What does exist:

- **PCI-DSS 4.0 and SOC 2 control-coverage reports** are GA on the hosted
  platform (Teams+). These are derived from live security findings with
  per-control evidence drill-ins and JSON / Markdown export. They are framed
  in-product and in every export as **coverage signals, not an audit or a
  certification**. Controls that automated findings cannot evidence are marked
  for manual attestation rather than silently passed.
- **ISO 27001 Annex A** and **GDPR / data-residency mappings** are on the
  extended roadmap, not shipped.
- **SBOM (CycloneDX 1.6) with per-dependency license detection, plus VEX
  export** is GA on the hosted platform (Pro+). SPDX and cross-format conversion
  are roadmap.
- **Audit trail** covering the hosted security surface (scans, vulnerability and
  secret views, SBOM/VEX exports, compliance views, finding-status changes, and
  MCP reads by AI agents) is insert-only with user, IP, and timestamp,
  exportable to JSON/CSV, with an opt-in signed-webhook stream. Coverage beyond
  the security surface is in development.
- **Audit rights.** The commercial license grants you the right to audit
  Repowise's own security and compliance practices
  ([COMMERCIAL.md §7.1](COMMERCIAL.md#71-what-the-commercial-license-grants)).
- **IP indemnification and a defensive patent grant** are GA under the
  commercial license.

**Licensing, since it usually shares a review cycle:** the core engine is
AGPL-3.0, free for individuals, teams, and companies using it internally. A
commercial license removes the AGPL obligations for embedding Repowise in your
own product and adds the layer above.

---

## 7. Questions your security team will ask

| Question | Answer |
|---|---|
| Does our source code leave our infrastructure? | Not in the self-hosted distribution. Code is read from disk and parsed in memory. The only path that can send code-derived content out is the LLM call for documentation, chat, and decision extraction, and that goes to the provider you configured with your key. `--index-only` and offline mode remove it entirely. |
| Is raw source stored anywhere? | No. Raw source is processed transiently and never persisted. The index holds the graph, embeddings, wiki pages, git metadata, decisions, and health data. |
| Can the embeddings be reversed back into our code? | They are non-reversible vectors, not encrypted source. They are still derived from your code, so protect the index directory like the repo. |
| Can we run with no network access at all? | Yes. Ollama plus a local embedding model gives zero external API calls; disable telemetry and the process makes no outbound requests. In air-gapped mode the LLM provider and git server sit inside your boundary. |
| Do you see our LLM API traffic? | No. BYOK calls go directly from your infrastructure to your provider. We do not proxy them. |
| What is your data retention on LLM prompts? | Governed by your own provider contract (for example Anthropic's API zero-retention policy), not by us. |
| What telemetry is sent, and can we turn it off? | Anonymous command names and coarse environment only, with no code, paths, repo names, flag values, IPs, or identifiers. Disable via `repowise telemetry disable`, `DO_NOT_TRACK=1`, `REPOWISE_TELEMETRY_DISABLED=1`, or by running offline. Verify the exact payload with `REPOWISE_TELEMETRY_DEBUG=1`. |
| How long is telemetry retained? | 90 days, then deleted. Only aggregates are used. |
| Can telemetry be tied back to a user or a repo? | No. `anon_id` is a random UUID in `~/.repowise/platform.json`, not derived from any machine or user identifier, and no repo-identifying field is sent. |
| Are you SOC 2 or ISO 27001 certified? | No. We publish PCI-DSS 4.0 and SOC 2 **control-coverage reports** on the hosted platform, explicitly labeled as coverage signals rather than an audit or certification. ISO 27001 Annex A mapping is roadmap. |
| Can we deploy on-prem or air-gapped? | Yes. Containerized API, indexer workers, and dashboard, backed by Postgres and LanceDB/pgvector, with optional Ollama. No outbound connectivity is required in air-gapped mode. A single packaged air-gapped bundle is planned, not shipped. |
| Do you support SSO and SCIM? | SAML / OIDC SSO (Okta, Entra ID, Auth0, Google Workspace, generic SAML 2.0) and SCIM provisioning are rolling out commercially. RBAC and multi-tenant are planned. Confirm current status against [COMMERCIAL.md §4](COMMERCIAL.md#4-commercial-capabilities--at-a-glance). |
| Is there an audit log? | On the hosted platform, insert-only, covering the security surface, with user, IP, and timestamp, JSON/CSV export, and an opt-in signed-webhook stream. Broader coverage is in development. |
| Do you store secrets you find? | Only a fingerprint and a redacted preview. Never the secret value. |
| What does the MCP server expose to an AI agent? | Read-only query tools over the local index. The lean MCP container exposes no ports and speaks stdio only. |
| What license are we accepting? | AGPL-3.0 for the OSS engine, free for internal use. A commercial license is required to embed Repowise in a product without AGPL obligations. |
| Who do we contact for a security review? | [security@repowise.dev](mailto:security@repowise.dev). Commercial scoping: [hello@repowise.dev](mailto:hello@repowise.dev). |

---

## 8. Reporting a vulnerability

Send it to [security@repowise.dev](mailto:security@repowise.dev). Please include
reproduction steps and the affected version (`repowise --version`).
