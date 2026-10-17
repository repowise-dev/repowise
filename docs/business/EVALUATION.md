# Evaluating Repowise for Your Company

Written for a platform, AppSec or architecture lead trying Repowise on their own code
before a wider rollout. Everything below runs on one machine with the open-source
package and no API key. No code, index or report leaves that machine.

Plan an afternoon. Pick one repository you know well, so you can judge the answers.

## 1. Index it, offline

```bash
uv tool install repowise          # or: pipx install repowise
export DO_NOT_TRACK=1             # switches off anonymous CLI telemetry too
cd /path/to/your/repo
repowise init --yes --no-prose    # graph, git history, health, tests, dead code
repowise serve                    # local dashboard and MCP server
```

`--no-prose` makes no model calls. Indexing time and memory by repository size are in
[BENCHMARKS.md](../BENCHMARKS.md#scale).

## 2. Check what it says about code you know

Open the dashboard and look at Code Health, Fix first and Dead code, or run
`repowise next` and `repowise dead-code`. After indexing, Repowise reports how many of
its lowest-scoring files had bug fixes in your own recent history, which tells you
whether the health score holds on your code.

## 3. Name your components

Declare two or three components, including one you consider critical, in
`.repowise/components.yaml`, or point Repowise at your existing `catalog-info.yaml`.

```bash
repowise components check
repowise components ownership     # declared owners against the last 90 days
```

See [Components and ownership](../layers/COMPONENTS.md).

## 4. Read a few recent changes

```bash
repowise changes list --limit 10
repowise changes show <id>        # the evidence checklist for one change
```

Pick a change you remember and check the components, owners, reaching tests and
contracts it lists. See [Change Records](../layers/CHANGES.md).

## 5. Write one policy and run it

Add a rule to `.repowise/policies.yaml`, for example that changes to the critical
component need reaching tests, and run it the way CI would:

```bash
repowise check HEAD~1..HEAD --sarif-file policy.sarif
```

See [Change policy](../layers/POLICY.md) and [Repowise in CI](../start/CI.md).

## 6. Produce the packs you would hand over

```bash
repowise report assessment --format html --out assessment.html
repowise report changes --format html --out changes.html
```

Read them as the auditor, acquirer or committee would. Every item says whether it is
measured, inferred, declared or unresolved. See [Evidence reports](../layers/REPORTS.md).

## 7. Optional: more than one repository

Add a second repository that talks to the first and see the contracts between them and
the cross-repository blast radius. See [Workspaces](../scale/WORKSPACES.md).

## Before a wider rollout

- Security review: [SECURITY_COMPLIANCE.md](SECURITY_COMPLIANCE.md) answers what is
  stored, what can leave the network and how a shared server authenticates callers.
- Commercial licence, on-prem topology and support: [COMMERCIAL.md](COMMERCIAL.md).
- Questions: [hello@repowise.dev](mailto:hello@repowise.dev).
