# Components and Ownership

A component is a part of the codebase your organisation names: a service, a library,
the payments module. Health, hotspots, bus factor, test reach, dead code and changes
all roll up to components, so a finding reads as "the payments component" and not as a
list of files.

## Declaring components

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
top-level directories, so every repository has them from the first index. A repo
those do not divide (every file at the root, or only layout and test directories
such as `src` and `test`) is one component named after the repo. A full
example is in [examples/components](../../examples/components/components.yaml).

## Declared owners against recent activity

Owners come from the catalog, CODEOWNERS (GitHub, and GitLab with sections) and an
optional `.repowise/teams.yaml`. `repowise components ownership` compares them with the
last 90 days of commits and flags a critical component with no active owner, an owner
with no recent changes on its files, a bus factor of 1 on a critical component, and a
catalog entry that claims no files. Findings name components and teams. Individuals
are counted, never named.

## Changes over time

Every `init` and `update` stores a snapshot per component. What moved since the last
run becomes a typed event (ownership shift, bus factor drop, new hotspot, contract
broken, decision stale, dead-code growth, test reach drop), written as CloudEvents to
`.repowise/events.jsonl` and listed by `repowise events`.

## Packages

`repowise packages where <name>` lists where a third-party package is imported, with
the component, owner teams and reachability. `repowise packages export --format
cyclonedx` writes that usage as CycloneDX 1.6, and `repowise packages import` joins an
SBOM or advisory file you already have to the import sites. Nothing is fetched. See
[package usage](../reference/PACKAGE_USAGE.md).

```bash
repowise components list
repowise components ownership
repowise events --since 30d
repowise packages where log4j-core
```

Every field: [CONFIG.md](../reference/CONFIG.md#repowisecomponentsyaml) and the
[CLI reference](../reference/CLI_REFERENCE.md#repowise-components).
