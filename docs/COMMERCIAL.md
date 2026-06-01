# Repowise — Commercial Offering

Repowise is dual-licensed: the core engine is **AGPL-3.0 and free** for individuals,
teams, and companies using it internally; a **commercial license** adds the
enterprise security, compliance, governance, and operations layer — and removes the
AGPL obligations for anyone embedding Repowise in their own product.

This document covers what the open-source distribution includes, what the commercial
license adds on top, the honest GA / in-development / planned status of each
commercial capability, the on-premise deployment model, and the available pricing
models. Specific pricing figures are provided in a separate proposal.

> **Looking for the open-source feature set?** The [README](../README.md) and
> [docs/](.) cover everything in the AGPL distribution. This document is about what
> sits *on top* of it commercially.

---

## 1. Who the commercial license is for

The open-source distribution covers the full developer-experience surface — the five
intelligence layers, the nine MCP tools, multi-repo workspaces, the local dashboard,
auto-sync, and auto-generated `CLAUDE.md`. That is everything an engineer or a team
needs to make their AI coding agents codebase-aware.

The commercial license is for organizations that need to roll Repowise out **at
scale, in a regulated or security-sensitive environment**:

- Large, long-lived codebases — often multi-language, multi-repo, with deep tribal
  knowledge that walks out the door when senior engineers leave.
- Security and compliance obligations (PCI-DSS, SOC 2, audit trails, SBOMs) that
  demand traceable rationale for architectural change, not just code diffs.
- Platform/security/compliance teams that require SSO, RBAC, audit logging, and
  on-prem or air-gapped deployment before a tool can touch source code.
- Product teams that want to **embed** Repowise intelligence in their own internal
  developer platforms without taking on AGPL obligations.

---

## 2. What ships in open source (AGPL-3.0)

All of the following ship in `pip install repowise` today, free for internal use.

- **Five intelligence layers** — Graph (tree-sitter AST across 15 languages, two-tier
  dependency graph, call resolution, heritage extraction, Leiden communities,
  PageRank / betweenness / SCC), Git (hotspots, ownership, co-change pairs, bus
  factor, significant commits, contributor profiles, module health), Documentation
  (LLM-generated wiki, freshness scoring, RAG search), Decision (architectural
  decision records linked to graph nodes, staleness tracking), and Code Health
  (25 deterministic biomarkers, 1–10 score per file, coverage ingestion, trend
  alerts).
- **Nine task-shaped MCP tools** — `get_overview`, `get_answer`, `get_context`,
  `get_symbol`, `search_codebase`, `get_risk`, `get_why`, `get_dead_code`,
  `get_health`. Benchmarked at **−36 % cost / −49 % tool calls** on `pallets/flask`
  and **−29 % cost / −70 % tool calls** on `scikit-learn` versus a strong baseline
  agent, at parity answer quality — see [repowise-bench](https://github.com/repowise-dev/repowise-bench).
- **Multi-repo workspace intelligence** — cross-repo co-changes, API contract
  extraction (HTTP / gRPC / topics) with provider↔consumer matching, package
  dependency mapping, federated MCP queries (`repo="all"`), workspace dashboard and
  `CLAUDE.md`.
- **Proactive agent hooks** — PreToolUse graph enrichment on every `Grep`/`Glob`;
  PostToolUse stale-wiki detection after `git commit`. No LLM calls, pure local
  SQLite.
- **Auto-sync** — post-commit hook, file watcher, GitHub webhook, GitLab webhook,
  polling fallback. Typical incremental update touches 3–10 pages in under 30 s.
- **Local dashboard** — Chat, Docs, Graph, C4, Search, Symbols, Coverage, Risk,
  Contributors, Module Health, Hotspots, Dead Code, Decisions, Costs, Blast Radius,
  Security (local pattern scan), Knowledge Map, and the workspace views.
- **Dead-code detection** — pure graph traversal, confidence-tiered, framework-aware
  (ASP.NET, Django, FastAPI, Flask, Rails, Laravel), dynamic-import aware.
- **Privacy** — self-hosted; source never leaves your infrastructure; BYOK or fully
  offline via Ollama; zero telemetry. Stored: graph, non-reversible embeddings, wiki
  pages, git metadata, decision records. Raw source is processed transiently and
  never persisted.

---

## 3. First-class language coverage

Repowise treats **9 languages at Full tier** — Python, TypeScript, JavaScript, Java,
Kotlin, Go, Rust, C++, and **C#** — with AST parsing, import resolution, named
bindings, call resolution, heritage extraction, multi-project workspace resolvers,
framework-aware edges, and per-language dynamic-hint extractors. A further 5 languages
(C, Ruby, Swift, Scala, PHP) sit at Good tier.

For estates built on a particular stack, the relevant Full-tier capabilities are
worth calling out. For **.NET**, as one example:

| Capability | Detail |
|------------|--------|
| Project graph | `.csproj`, `.sln`, `Directory.Build.props` parsed for `<ProjectReference>` / `<PackageReference>` |
| Multi-project workspace | Namespace → file mapping propagated across projects in a solution |
| Framework-aware edges | ASP.NET MVC / Minimal API, EF Core `DbContext`, gRPC-dotnet services |
| Dynamic-hint extraction | .NET DI registrations, `Activator.CreateInstance`, reflection, `InternalsVisibleTo` |
| Documentation | XML doc comments (`/// <summary>`) extracted into the wiki |
| Contract extraction | ASP.NET HTTP routes and gRPC-dotnet service defs surfaced as workspace contracts |
| Dead code | Confidence-tiered, respecting ASP.NET / EF Core decorators and DI registrations |

The same depth exists for the other Full-tier languages. The language pipeline is
modular by design — adding or deepening a language touches per-language subpackages,
not the parser core — which is what makes the **custom language / framework
extensions** in §5.4 a tractable commercial offering.

---

## 4. Commercial capabilities — at a glance

Status is honest: **GA** (generally available today), **dev** (working internals,
limited customer-facing surface), or **planned** (near-term roadmap). Sequencing
against your procurement timeline is agreed as part of the commercial proposal —
the items that matter most to you can be prioritized.

| Capability | Open Source (AGPL) | Commercial License |
|------------|:------------------:|:------------------:|
| Five intelligence layers | ✅ | ✅ |
| Nine MCP tools | ✅ | ✅ |
| Multi-repo workspaces | ✅ | ✅ |
| Full-tier language support (incl. C# / .NET) | ✅ | ✅ |
| Local dashboard (incl. local security pattern scan) | ✅ | ✅ |
| Auto-sync (hooks, watcher, webhooks) | ✅ | ✅ |
| Auto-generated CLAUDE.md | ✅ | ✅ |
| Graph-aware enhanced security scanning | — | ✅ *(dev)* |
| Language-specific security rulesets | — | ✅ *(dev)* |
| CVE-aware dependency analysis | — | ✅ *(planned)* |
| Reachability-aware CVE triage | — | ✅ *(planned)* |
| SBOM generation (CycloneDX) | — | ✅ *(planned)* |
| Compliance reporting (PCI-DSS / SOC 2) | — | ✅ *(planned)* |
| Audit trail (in-product + JSON / CSV export) | — | ✅ *(dev)* |
| Jira / Confluence integration | — | ✅ *(rolling out)* |
| GitHub Enterprise / Azure DevOps / GitLab / Bitbucket | — | ✅ *(rolling out)* |
| Slack / Teams alerting | — | ✅ *(rolling out)* |
| SAML / OIDC SSO + SCIM | — | ✅ *(rolling out)* |
| RBAC + multi-tenant | — | ✅ *(planned)* |
| Air-gapped install bundle | — | ✅ *(planned)* |
| Reference HA topology | — | ✅ *(GA on customer infra)* |
| Engineering leader dashboard | — | ✅ *(rolling out)* |
| Custom language / framework extensions | — | ✅ *(GA)* |
| Priority support & SLA | — | ✅ *(GA)* |
| IP indemnification + defensive patent grant | — | ✅ *(GA)* |

---

## 5. Commercial capabilities — in detail

### 5.1 Security & Compliance

- **Security scanning layer** *(GA: local pattern scan; dev: graph-aware
  enrichment)* — pattern-based detection for dangerous APIs (`eval`/`exec`,
  `pickle.loads`, `shell=True`, `os.system`, hardcoded secrets, concat / f-string
  SQL, `verify=False`, weak hashes) runs locally today in the dashboard's Security
  view. Graph-aware enrichment — linking findings to graph nodes and surfacing them
  through `get_risk` so AI agents see security context before modifying a file — is
  in development.
- **Language-specific security rulesets** *(dev)* — rulesets built on top of the
  per-language dynamic-hint extractors and framework edges. For .NET, planned checks
  include `[Authorize]` coverage on controllers and Minimal API endpoints,
  `IConfiguration` secret leakage, EF Core raw-SQL risk, `HttpClient` lifetime
  issues, and `AllowAnonymous` on sensitive routes. Each language's ruleset ships as
  a focused subset, then expands on customer feedback.
- **CVE-aware dependency analysis** *(planned)* — dependency manifests
  (`*.csproj`, `packages.lock.json`, `package.json`, `pyproject.toml`, `go.mod`)
  matched against NVD / GitHub Advisory / OSV feeds, with severity, fix availability,
  and transitive-impact scoring.
- **Reachability-aware CVE triage** *(planned)* — because Repowise holds a resolved
  call graph, CVEs can be classified by whether the vulnerable function is actually
  reachable from your code, reducing SCA noise. Precision is language- and
  pattern-dependent; we report it honestly per language rather than quoting one
  global number.
- **SBOM generation** *(planned)* — CycloneDX output per commit with per-dependency
  license detection and SBOM diffs between releases. SPDX and cross-format conversion
  on the extended roadmap.
- **Compliance reporting** *(planned)* — framework-mapping reports tying findings
  back to specific files, owners, and decisions. Initial scope: **PCI-DSS** and
  **SOC 2** control coverage. ISO 27001 Annex A and GDPR / data-residency mappings on
  the extended roadmap — we'd rather ship two solid mappings than four shallow ones.
- **Audit trail** *(dev)* — every decision, override, security-finding action, and
  false-positive resolution logged with user, timestamp, and rationale. Queryable
  in-product and exportable to JSON / CSV; streaming export to SIEM (Splunk / Datadog
  / Elastic / syslog) on the roadmap.
- **Secret-in-code detection** *(planned)* — gitleaks-style scanning across full git
  history (not just `HEAD`), integrated with the graph so leaked secrets surface
  which services / modules referenced them.

### 5.2 Workflow Integrations *(rolling out)*

The plumbing these sit on — audit trail, RBAC, the commercial event bus — is in
development. The connectors themselves are sequenced by customer demand; additional
integrations beyond this list are available on request.

- **Jira** — bi-directional linking between architectural decisions / risk findings
  and Jira issues; `get_why` surfaces the originating ticket; PR-impact reports
  auto-comment on the linked issue.
- **Confluence** — scheduled publication of the Repowise wiki to nominated spaces,
  with link-backs preserved and freshness banners on stale pages.
- **GitHub Enterprise / Azure DevOps / GitLab / Bitbucket** — managed webhooks, a
  PR-comment bot that posts blast-radius and reviewer suggestions, and a
  branch-protection check that blocks merges touching hotspots without a reviewer
  from the ownership list.
- **Slack & Microsoft Teams** — alerts on hotspot drift, bus-factor warnings,
  decision staleness, and security findings, routed by ownership.
- **SAML / OIDC SSO** — Okta, Entra ID, Auth0, Google Workspace, generic SAML 2.0.
- **SCIM provisioning** — automatic user / group lifecycle.

### 5.3 Engineering Leadership & Governance

The underlying signals (ownership, bus factor, hotspot trends, decision staleness)
are already computed and queryable today via the OSS dashboard; the leadership-facing
presentation and policy layer is what's rolling out commercially.

- **Engineering leader dashboard** *(rolling out)* — bus-factor trends, hotspot
  evolution over time, cross-repo dead code, ownership drift, decision-staleness
  curves, scheduled email digests (weekly / sprint / monthly / executive).
- **Session intelligence harvesting** *(planned)* — architectural decisions surfaced
  from AI coding sessions and proposed to the team knowledge base, so tribal
  knowledge generated *during* AI-assisted work doesn't evaporate when the session
  ends.
- **Shared team context layer** *(dev)* — one `CLAUDE.md` backed by the full graph
  and decision layer, auto-injected into every team member's IDE / agent session via
  MCP. Every engineer's agent starts from the same institutional context.
- **Cross-repo intelligence at scale** *(dev)* — hotspots, dead code, and ownership
  across the entire estate with centralized dashboards (beyond the local-workspace
  scope already shipping in OSS).
- **Custom decision policies** *(planned)* — required-reviewer rules, mandatory
  `get_why` checks for governed paths, and merge-gating policies tied to Repowise
  findings.

### 5.4 Enterprise Operations

- **Role-based access control** *(planned)* — repo-, module-, and decision-level
  permissions; SCIM group mapping.
- **Multi-tenant deployment** *(planned)* — segregate engineering orgs / product
  lines while sharing cross-cutting infrastructure repos.
- **Air-gapped install bundle** *(planned)* — packaged install with bundled grammars,
  embedding model, and optional Ollama runtime for fully offline environments.
- **Reference HA topology** *(GA on customer infra; managed scaling tooling:
  planned)* — Postgres-backed metadata store, S3-compatible artefact storage, and
  horizontally scalable MCP servers.
- **Backup & restore tooling** *(planned)* — point-in-time snapshots of the
  intelligence layers.
- **Priority support & SLA** *(GA)* — named support contact, response-time SLA, and a
  quarterly architecture-review session.
- **Custom language / framework extensions** *(GA)* — bespoke tree-sitter grammars or
  framework edges, packaged and maintained by the Repowise team.
- **IP indemnification** *(GA)* — protection against third-party IP claims related to
  the Repowise software.
- **Defensive patent grant** *(GA)*.

---

## 6. On-Premise Deployment

A self-hosted commercial install runs as a set of containers on your own
infrastructure. The reference topology is Kubernetes-based, but the same containers
run on Nomad or plain Docker — a Helm chart is on the near-term roadmap.

1. **Containerised services** — Repowise API server (FastAPI), indexer workers, and
   dashboard (Next.js), backed by Postgres for metadata and LanceDB (or pgvector) for
   embeddings. An optional Ollama container provides fully-offline LLM use.
2. **Webhook receivers** for GitHub Enterprise / GitLab self-managed / Azure DevOps,
   configured against each tracked repository.
3. **SSO** wired through your existing Entra ID / Okta tenant (per §5.2 availability).
4. **Outbound integrations** (Jira, Confluence, Slack / Teams — per §5.2
   availability) configured via signed service tokens stored in the Repowise secret
   store.
5. **BYOK** for the LLM — your Anthropic / OpenAI enterprise contract, Azure OpenAI in
   your tenant, or fully offline Ollama. The choice can be made per-repository, so
   sensitive repos run fully offline while less sensitive tooling repos use a hosted
   model.

**Topology at a glance:** all Repowise services run inside your VPC or air-gapped
network, backed by Postgres for metadata, LanceDB (or pgvector) for embeddings, and
an in-memory NetworkX graph. The LLM provider and the git server sit alongside
Repowise in the same network boundary — no outbound connectivity is required in
air-gapped mode.

**Indexing:** the graph, git, dead-code, and code-health layers build in minutes with
zero LLM calls (`repowise init --index-only`); the documentation layer's one-time
wiki generation scales with repo size and can run in the background. Incremental
updates after each commit complete in under 30 seconds.

---

## 7. Licensing & Pricing

### 7.1 What the commercial license grants

1. **Proprietary modification rights** — modify Repowise source without releasing
   modifications under AGPL.
2. **Embedding rights** — embed Repowise intelligence in your internal tooling and
   developer platforms.
3. **Patent grant** — defensive patent grant covering Repowise's methods and
   algorithms.
4. **IP indemnification** — protection against third-party IP claims.
5. **Support & maintenance** — dedicated engineering support with SLA-backed response
   times.
6. **Update rights** — all updates and new features during the license term.
7. **Audit rights** — right to audit Repowise's security and compliance practices.

### 7.2 Pricing models

Commercial pricing is **flexible across three models** — pick whichever maps best to
your procurement and org structure. Specific figures are provided in a separate
proposal.

| Model | How it scales | Best for |
|-------|---------------|----------|
| **Per-seat** | Priced per engineer with dashboard / API / MCP access. Inactive seats reclaimable. SCIM-managed lifecycle. | Spend tracks headcount; clean alignment with existing dev-tools billing (IDE licences, Copilot, etc.). |
| **Per-repo** | Priced per indexed repository (workspace cross-repo intelligence included). Seats unlimited within the licensed repo set. | The value driver is codebase footprint, not headcount — useful when Repowise is consumed primarily via AI agents and CI rather than human dashboard use. |
| **Enterprise-wide** | Unlimited seats, unlimited repos, all commercial features, on-prem / air-gapped, named support, MFC clause. | Org-wide standardisation. Removes per-repo accounting overhead and aligns with single-procurement contracts. |

All three models include the full set of §5 commercial features; the difference is
purely the scaling dimension. Hybrid arrangements (e.g. enterprise-wide for one
business unit, per-repo elsewhere) are available.

---

## 8. Get in touch

Commercial-proposal requests, scoping questions, and security reviews:

- General / commercial: [hello@repowise.dev](mailto:hello@repowise.dev) ·
  [repowise.dev/#contact](https://www.repowise.dev/#contact)
- Security-specific: [security@repowise.dev](mailto:security@repowise.dev)
