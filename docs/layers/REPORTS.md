# Components, policy and reports

Three layers that turn the index into something an organisation can act on.
**Components** name the parts of the codebase your organisation owns (a service,
a library, the payments module), so health, hotspots, bus factor, test reach,
dead code and changes all roll up to "the payments component" and not to a list
of files. **Change policy** holds some changes to a higher bar than others, with
rules that live next to the code. **Evidence reports** (`repowise report`) write
documents a team hands to someone else: an auditor, an acquirer, a board or a
migration committee.

All three are built from the local index. Nothing is sent anywhere.

## Quick start

```bash
repowise components list
repowise components ownership
repowise events --since 30d
repowise packages where log4j-core

repowise check main..HEAD

repowise report assessment --format html --out assessment.html
repowise report changes --since 2026-07-01 --format markdown --out q3-changes.md
repowise report inventory --format csv --out inventory.csv
repowise report --since-last
```

Components need no setup: with no declaration file, every repository has them
from the first index. A policy needs a `.repowise/policies.yaml`.

## Reading the results

### Components and ownership

#### Declaring components

Components follow the Backstage model (`Component`, `System`, `Group`). Declare them in
`.repowise/components.yaml`, or let Repowise read the `catalog-info.yaml` your developer
portal already uses. `repowise.dev/*` annotations add paths, criticality, tags and
business capability.

```yaml
apiVersion: backstage.io/v1alpha1
kind: Component
metadata:
  name: payments
  annotations:
    repowise.dev/paths: "services/payments/**"
    repowise.dev/criticality: critical
    repowise.dev/tags: "pci, customer-facing"
spec:
  type: service
  owner: group:default/payments-team
  lifecycle: production
```

With no file, components are derived from workspace services, module pages or
top-level directories. A repo those do not divide (every file at the root, or only
layout and test directories such as `src` and `test`) is one component named after
the repo. A full example is in
[examples/components](../../examples/components/components.yaml).

#### Declared owners against recent activity

Owners come from the catalog, CODEOWNERS (GitHub, and GitLab with sections) and an
optional `.repowise/teams.yaml`. `repowise components ownership` compares them with the
last 90 days of commits and flags a critical component with no active owner, an owner
with no recent changes on its files, a bus factor of 1 on a critical component, and a
catalog entry that claims no files. Findings name components and teams. Individuals
are counted, never named.

#### Changes over time

Every `init` and `update` stores a snapshot per component. What moved since the last
run becomes one of nine typed events: ownership shift, owner inactive, bus factor drop,
new hotspot, contract added, contract broken, decision stale, dead-code growth and test
reach drop. They are written as CloudEvents to `.repowise/events.jsonl` and listed by
`repowise events`. A component compares only with a run that defined it the same way,
and each rule needs a measured basis on both sides and a size floor, so a re-drawn
component never reads as its facts moving.

#### Packages

`repowise packages where <name>` lists where a third-party package is imported, with
the component, owner teams and reachability. `repowise packages export --format
cyclonedx` writes that usage as CycloneDX 1.6, and `repowise packages import` joins an
SBOM or advisory file you already have to the import sites. Nothing is fetched. See
[package usage](../reference/PACKAGE_USAGE.md).

### Change policy

Rules live in `.repowise/policies.yaml`, next to the code they govern, and
`repowise check` evaluates them against any change, locally or in CI.

```yaml
version: 1
rules:
  - name: payments-need-tests
    match:
      components: {tags: [pci]}
    when:
      reaching_tests: missing
    action: require_tests
  - name: no-silent-contract-breaks
    when:
      contract_break: true
    action: fail
  - name: no-new-secrets
    gate: security
    with: {fail_on: high}
    action: fail
```

A rule selects [components](#components-and-ownership) by name, tag or criticality,
then tests conditions over the change's [evidence checklist](CHANGES.md): provenance,
reaching tests, contract breaks, decisions crossed and bus factor. A rule can also wrap
an existing gate (`security`, `coverage`, `doc_drift` or `health`), which then runs
exactly as the command does on its own. There is no risk-score condition: rules rest on
evidence you can check.

A condition whose evidence is not available, for example with no index, is reported
as not evaluated and never fires.

Actions are `warn`, `require_owner_review`, `require_tests` and `fail`. Only `fail`
exits 1; the others are reported in every format.

### Evidence reports

JSON is the canonical form, with Markdown, CSV and a self-contained HTML file that
opens offline.

| Report | What it holds | Typical reader |
|---|---|---|
| `report assessment` | Component posture, hotspots, bus factor, dead code by tier, test reach, contracts, governing decisions, provenance coverage and what moved in the last quarter | Acquirer, board, platform lead, migration committee |
| `report changes` | Every change merged in a period: components and owning teams, review record, reaching tests, policy verdict, provenance and later outcome, with a period summary | Auditor reviewing change management (SOC 2 CC8.1, ISO 27001 A.8.32, PCI DSS 6.5) |
| `report inventory` | Components with owner teams, languages, entry points and the packages they import | Software inventory review (PCI DSS 6.3.2) |
| `report repo` | What Repowise found, most urgent first: each item with its path and line, a link into the app, an agent prompt and its status (`repowise mark`). Written by `init`, free | The developer who just indexed the repo, and their agent |
| `report estate` | A workspace's repos and services, the system map and architecture with the evidence behind them, contracts and the calls that reached no provider, health and churn per repo, the riskiest seams and where to start | Platform lead, acquirer, team taking over an estate |

In a workspace, `report assessment` and `report inventory` cover every repository.
`report estate` covers the workspace as one system, from what `repowise update --workspace`
stored.

#### The repo report

`init` writes it once, after the first index, to `.repowise/reports/`: the first look (the
baseline, `first-look.json`) and its exports `repo-report.md` and `repo-report.html`, and names it on
one line of the finish panel (for an agent or a run nobody watches, that line says to relay
`repowise report --format md` to the person, and `--progress json` carries a `report` object). `repowise
report` (also `report repo`, alias `report first-look`) opens it as a living report: the first look as the index has it now, each item with its status, what is
new since and what was resolved since the first look. `--format json` hands its items to an agent, `--prompt`
prints the whole-report agent prompt, and `--since-last` lists what was fixed since. Its sentences come from one template table over the stored findings (no model,
no cost); links, paths and prompts come from finding ids. Items one fix covers (a hard-coded
password and a hard-coded secret) are one item with every location. Sections are providers on
`SeamRegistry("first-look-section")` and its renderers sit on `report-renderer` as
`first-look-markdown` and `first-look-html`; the Markdown and JSON exports carry no prompts.

`repowise mark ID done|ack|false-positive|snooze` records an answer to one item, on every row
it stands for, in the stores the web app and `repowise next` read (next actions, dead-code,
health and refactoring statuses). `ack` and `false-positive` need `--reason`, kept in each of those stores. A security finding
is never marked a false positive there: a `repowise-security-ignore` comment on its line, or the
security baseline, says so. The report's "Work through this report" prompt (`repowise report
--prompt`) lists the open items with their ids and asks the agent to mark each one.

A model writes the report's wording once, at `init` when one is configured, and again only when
asked (`repowise report --llm`, which shows the cost first). `repowise update` and every read
reuse the stored wording for each item whose facts have not changed; a new or changed item reads
in template sentences. `prose_source` in the JSON says who wrote the wording, when, and what it
cost.

#### The change report period

```bash
repowise report changes [--since DATE|all] [--until DATE] [--no-build] [--refresh]
```

- With no `--since`, the period is the 90 days before the newest indexed commit (or before `--until`, when earlier). In a workspace, each repo's 90 days count back from its own newest commit.
- `--since all` covers every indexed change. `--until` is exclusive.
- A change with no kept evidence checklist gets one built and stored first, a few seconds each. `--no-build` builds none and counts those changes as unknown. `--refresh` rebuilds every checklist in the period.
- `--workspace` covers every workspace repo, `--repo ALIAS` one of them, and `--no-workspace` forces single-repo mode.

#### How to read them

Every item says how it is known: `measured`, `inferred`, `declared` or `unresolved`.
Missing evidence is reported as unresolved, never as a low score or a pass. Assessment
items keep the OpenSSF Scorecard check shape, so existing tooling can read them. The
change report ends with a method appendix that states what git alone cannot attest,
such as a review that left no trace in the history.

## Tuning

Components are declared in `.repowise/components.yaml` (or the `catalog-info.yaml`
your portal uses) and teams in `.repowise/teams.yaml`; policy rules in
`.repowise/policies.yaml`. Every key:
[components.yaml](../reference/CONFIG.md#repowisecomponentsyaml) and
[policies.yaml](../reference/CONFIG.md#repowisepoliciesyaml).

## In CI

`repowise check BASE..HEAD` runs the policy on a pull request and exits 1 only when a
`fail` rule fires. `--sarif-file` writes SARIF 2.1.0 for GitHub code scanning or
reviewdog, and `--junit-file` writes JUnit XML for any CI test view. Teams that
already run OPA can pass the `evidence` object from `--format json` to Rego. The gates
a rule can wrap, and how to run each one in CI, are in [CI.md](../start/CI.md).

```bash
repowise check main..HEAD --sarif-file policy.sarif --junit-file policy.xml
```

## Accuracy and limits

- A missing fact is never a pass: report items say `unresolved`, and a policy
  condition without its evidence is not evaluated.
- Owners and reviewers are counted, never ranked or named, and local paths are
  removed from reports.
- The reports describe code and components. They are evidence for a review, not a
  certification.

## Where it shows up

- **CLI**: `repowise components`, `repowise events`, `repowise packages`,
  `repowise check`, `repowise report` and `repowise mark`.
- **Files**: `.repowise/events.jsonl` (CloudEvents), `.repowise/reports/` (the repo
  report), and the SARIF, JUnit, JSON, Markdown, CSV and HTML files the commands write.
- **Plugins**: report sections and renderers are plugin seams, so an organisation can
  add its own section or output format.

## Reference

Every field, condition, exit code, section and option:
[CONFIG.md](../reference/CONFIG.md#repowisecomponentsyaml) and the CLI reference for
[`repowise components`](../reference/CLI_REFERENCE.md#repowise-components),
[`repowise check`](../reference/CLI_REFERENCE.md#repowise-check-revspec) and
[`repowise report`](../reference/CLI_REFERENCE.md#repowise-report-assessment-path).

## See also

- [INTELLIGENCE_LAYERS.md](INTELLIGENCE_LAYERS.md): every layer at a glance
- [CHANGES.md](CHANGES.md): the evidence checklist a policy and the change report read
- [Package usage](../reference/PACKAGE_USAGE.md): import sites and SBOM joins
