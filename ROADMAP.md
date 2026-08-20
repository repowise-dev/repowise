# Roadmap

What we are building next, and what we have decided not to build.

**There are no dates on this page.** We shipped twelve minor releases in the
thirty-one days to 2026-08-18, roughly one every two and a half days. Any
quarter printed here would be wrong in the slow direction, and a roadmap that is
wrong in the slow direction is worse than one carrying no dates at all. What we
commit to is the status column. The [changelog](docs/CHANGELOG.md) is the
evidence, and it is the fastest way to check whether this page is honest.

Items disappear from this page when they ship. If you are looking for something
that used to be here, it is in the changelog.

| Status | What it means |
|---|---|
| **In development** | Being worked on now. Code exists, it is not finished. |
| **Planned** | Committed to, not started. |
| **Exploring** | We think it is a good idea and have not committed to it. Tell us if you need it, that is what moves a row up. |

The same vocabulary describes the commercial surface in
[docs/business/COMMERCIAL.md](docs/business/COMMERCIAL.md), where each capability
also carries a **GA** marker when it is available today.

---

## Languages

**Every language repowise supports ships in the open-source distribution under
AGPL-3.0.** No language sits behind the commercial licence, and none will. If
your stack is on this list, the support arrives in `pip install repowise`.

Languages land on a five-rung ladder rather than a yes/no list, because "do you
support X" has five different useful answers. Each rung is defined, with what it
buys you, in
[docs/layers/LANGUAGE_SUPPORT.md](docs/layers/LANGUAGE_SUPPORT.md). Today that
ladder covers **35 languages, 19 of them parsed to a full AST**.

### New languages

| Language | Status | Why it is on the list |
|---|---|---|
| **COBOL** | **In development** | Asked for by enterprises with mainframe estates evaluating repowise. The codebases that most need an institutional-memory layer are often the ones whose authors have already retired. |
| **VB.NET** | Planned | Large, long-lived line-of-business estates, usually sitting beside the C# we already treat at Full tier. |
| **PL/SQL** | Planned | We already parse SQL through sqlglot. Packages, procedures and triggers are where the business logic actually lives. |
| **ABAP** | Exploring | The same argument as COBOL, in SAP estates. |
| **RPG / AS400** | Exploring | Named alongside COBOL often enough to track. |
| **Fortran** | Exploring | Scientific and engineering codebases with long lifespans and thin documentation. |
| **Apex** | Planned | Salesforce estates, where the org is often the least documented system a company runs. Apex is Java-shaped, and Lightning Web Components are already covered as JavaScript and HTML. |
| **Ada** | Exploring | Defense and aerospace, long-lived and thinly documented, and usually in the same estates asking about COBOL. |
| **Objective-C** | Planned | Currently Structural, meaning history only. The grammar is mature, so this is a climb up the ladder rather than new ground. |
| **Elixir**, **F#** | Planned | Currently Lightweight, meaning file-to-file imports. Both grammars are already on PyPI, so these are AST upgrades rather than new ground. |
| **Template dialects** (Django/Jinja, ERB, Blade, Thymeleaf, Go templates) | Planned | Today they parse cleanly as HTML and yield nothing, because `{% extends "base.html" %}` is plain text to an HTML parser. A stated ceiling, not an oversight. |

### Deepening languages we already parse

| Language | Status | Next |
|---|---|---|
| Vue, Svelte | In development | Options-API members, `{#each}` and `v-for` head bindings, `.svelte.ts` rune modules |
| C#, Scala, Ruby | Planned | Dataflow dialects, which is what Extract Method needs in order to lift a span safely |
| Kotlin | Exploring | Dataflow is blocked on the grammar and needs either a grammar upgrade or a text-based jump seam. We would rather say blocked than planned. |
| C | Planned | It shares the C++ grammar for parsing but reaches no health walker map, so it gets graph coverage without markers |
| SQL / dbt | Planned | Column-level blast radius |
| Object Pascal | Planned | Assertion and performance markers, a dedicated `uses` resolver |

### Need a language that is not here

Adding one touches per-language subpackages rather than the parser core, which
is what makes this tractable at the rate above. Open an issue and we will scope
it in public. Commercial customers can have a language or framework built and
maintained by us as a line item, which is
[GA today](docs/business/COMMERCIAL.md#54-enterprise-operations), and the result
still ships to everyone under AGPL.

---

## Source control beyond git

Plenty of the code most worth indexing has never been near a git remote. It sits
in Perforce, in Subversion, or on a mainframe under a change-management system
older than most of the people maintaining it.

**A repository with no git at all already indexes today.** Only one of the five
layers is derived from history; the graph, documentation, decisions and
code-health layers are all computed from the working tree. Point `repowise init`
at a plain directory, an export, or a Perforce or SVN workspace and those four
build normally. What is missing is the history-derived half: hotspots, ownership,
co-change, bus factor, bug history, and change risk, all of which need a commit
log to mine.

Closing that gap means teaching the history layer to read something other than
`git log`. The signals above it do not care where the history came from once it
is normalized into changes, authors, timestamps and touched files, which is the
shape every one of these systems has.

| System | Status | Shape of the work |
|---|---|---|
| **Perforce Helix Core** | Planned | Changelists map to commits almost directly, and `p4 filelog` and `p4 annotate` cover history and blame. Common in games, hardware, embedded and anywhere the repo carries large binaries. |
| **Subversion** | Planned | Revisions map cleanly; `svn log` and `svn blame` are close analogues. Still the system of record in a lot of long-lived estates. |
| **TFVC (Azure DevOps)** | Exploring | Changesets, for shops on Azure DevOps that never moved to its git repos. |
| **Mercurial** | Exploring | Changesets map cleanly, and the work is small. Ordered by demand rather than difficulty. |
| **CA Endevor SCM** (Broadcom) | Exploring | Not a commit DAG. Endevor tracks elements moving through a stage and environment promotion hierarchy, so the practical shape is an export to a filesystem plus a history adapter that reads element history rather than a log. Pairs with the COBOL estates above. |
| **ChangeMan ZMF** (OpenText) | Exploring | The same argument, with packages instead of elements. |

**Ask, do not guess, if your system is not listed.** The seam is the same for all
of them, and what decides the order is which ones customers actually run.

---

## Multi-repo and workspace

Real systems are not one repository, and the interesting failures live in the
gaps between them.

| Item | Status | What it adds |
|---|---|---|
| Cross-repo intelligence at scale | In development | Hotspots, dead code and ownership across a whole estate with centralized dashboards, beyond the local-workspace scope already shipping in open source |
| Column-level blast radius for SQL / dbt | Planned | Lineage is table-level today through `ref()` and `source()`. Column-level answers "who breaks if I drop this field" |
| Custom decision policies | Planned | Required-reviewer rules, mandatory `get_why` checks on governed paths, and merge gating tied to findings |

---

## Integrations

These sit on the hosted platform and the commercial licence. Current
availability, including everything that is GA today, is in
[COMMERCIAL.md §5.2](docs/business/COMMERCIAL.md#52-workflow-integrations-rolling-out).

| Item | Status |
|---|---|
| GitHub Enterprise, Azure DevOps, GitLab self-managed, Bitbucket | In development |
| SAML / OIDC SSO (Okta, Entra ID, Auth0, Google Workspace) and SCIM provisioning | In development |
| Slack and Microsoft Teams beyond security alerting: hotspot drift, bus-factor warnings, decision staleness, routed by ownership | In development |
| Jira risk-finding links and PR-impact auto-comments on linked issues | Planned |
| Native SIEM connectors (Splunk, Datadog, Elastic) beyond the signed-webhook stream | Planned |
| PagerDuty | Planned |
| SPDX SBOM output and cross-format conversion | Planned |
| ISO 27001 Annex A and GDPR / data-residency control mappings | Planned |

On that last row: we would rather ship two solid compliance mappings than four
shallow ones, which is why PCI-DSS 4.0 and SOC 2 are GA and these are not.

---

## Enterprise operations

| Item | Status |
|---|---|
| Role-based access control at repo, module and decision level | Planned |
| Air-gapped install bundle with bundled grammars, embedding model and optional Ollama runtime | Planned |
| Helm chart for the reference topology | Planned |
| Multi-tenant deployment | Planned |
| Backup and restore, point-in-time snapshots of the intelligence layers | Planned |
| Audit-trail coverage beyond the security surface, covering decisions and overrides | In development |
| Engineering leader dashboard: bus-factor trends, ownership drift, decision-staleness curves, scheduled digests | In development |

---

## Core intelligence

The half of the roadmap that has nothing to do with procurement.

| Item | Status | Note |
|---|---|---|
| Interface-dispatch recall | Exploring | The measured ceiling on our call-graph recall. On `syft`, 44% of what we miss is dynamic dispatch alone and a further 39% is dispatch with a closure at one end. Nobody in the [measured field](docs/BENCHMARKS.md#8-the-same-question-against-an-answer-key-we-do-not-control) has cleared it, at 6.5 possible targets per call site, and matching that recall naively means emitting six edges where one is right. |
| A sealed JavaScript / TypeScript retrieval corpus | In development | Built and half graded. The sealed half is unrun, and nothing from it is quoted anywhere until it is evaluated, once. |
| More compiler oracles for graph precision | Exploring | Go and TypeScript are done. C#, Java, Kotlin and C++ each need a toolchain and a working build per repository; Rust has the toolchain and no sound call-graph tool exists for it; Python, Ruby and PHP admit no oracle even in principle. The honest status is "probably not", not "planned". |
| Session intelligence harvesting | Planned | Architectural decisions surfaced from AI coding sessions and proposed to the team knowledge base, so knowledge generated during agent work does not evaporate when the session ends |

---

## What we are not building

- **An LLM-based PR reviewer.** The [PR bot](README.md#the-pr-bot) makes zero
  model calls per review, and that is the product rather than a limitation:
  nothing to hallucinate, nothing to prompt-inject, and pushing the same diff
  twice produces the same review twice. CodeRabbit and Greptile do the other
  thing, and do it well. If you want intent-reading review, run one of those
  alongside this.
- **Runtime or APM data.** Everything here is derived from source and git
  history. A performance finding we report is a static one, and we label it as
  static.
- **Any language behind a paywall.** Stated above, repeated here because it is
  the question we get asked most.
- **Dates.** See the top of this page.

---

## Ask for something

- Open an [issue](https://github.com/repowise-dev/repowise/issues) and say what
  you need and why. Demand is what moves an Exploring row to Planned, and we say
  so rather than implying the order is purely technical.
- Commercial and procurement timelines: [hello@repowise.dev](mailto:hello@repowise.dev)
- Security review: [security@repowise.dev](mailto:security@repowise.dev)
