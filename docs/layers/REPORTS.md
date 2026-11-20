# Evidence Reports

`repowise report` writes documents a team hands to someone else: an auditor, an
acquirer, a board or a migration committee. Each is built from the local index, and
JSON is the canonical form, with Markdown, CSV and a self-contained HTML file that
opens offline.

| Report | What it holds | Typical reader |
|---|---|---|
| `report assessment` | Component posture, hotspots, bus factor, dead code by tier, test reach, contracts, governing decisions, provenance coverage and what moved in the last quarter | Acquirer, board, platform lead, migration committee |
| `report changes` | Every change merged in a period: components and owning teams, review record, reaching tests, policy verdict, provenance and later outcome, with a period summary | Auditor reviewing change management (SOC 2 CC8.1, ISO 27001 A.8.32, PCI DSS 6.5) |
| `report inventory` | Components with owner teams, languages, entry points and the packages they import | Software inventory review (PCI DSS 6.3.2) |
| `report first-look` | What Repowise found at the first index, most urgent first: each item with its path and line, a link into the app and an agent prompt. Written by `init`, free | The developer who just indexed the repo, and their agent |
| `report estate` | A workspace's repos and services, the system map and architecture with the evidence behind them, contracts and the calls that reached no provider, health and churn per repo, the riskiest seams and where to start | Platform lead, acquirer, team taking over an estate |

```bash
repowise report assessment --format html --out assessment.html
repowise report changes --since 2026-07-01 --format markdown --out q3-changes.md
repowise report inventory --format csv --out inventory.csv
repowise report first-look --since-last
```

In a workspace, `report assessment` and `report inventory` cover every repository.
`report estate` covers the workspace as one system, from what `repowise update --workspace`
stored.

## The first-look report

`init` writes it once, after the first index, to `.repowise/reports/` as JSON, Markdown and
HTML, and names it on one line of the finish panel. `repowise report first-look` opens it;
`--format json` hands it to an agent; `--since-last` lists what was fixed since. Its sentences
come from one template table over the stored findings (no model, no cost); links, paths and
prompts come from finding ids. Sections are providers on `SeamRegistry("first-look-section")`
and its renderers sit on `report-renderer` as `first-look-markdown` and `first-look-html`.

## The change report period

```bash
repowise report changes [--since DATE|all] [--until DATE] [--no-build] [--refresh]
```

- With no `--since`, the period is the 90 days before the newest indexed commit (or before `--until`, when earlier). In a workspace, each repo's 90 days count back from its own newest commit.
- `--since all` covers every indexed change. `--until` is exclusive.
- A change with no kept evidence checklist gets one built and stored first, a few seconds each. `--no-build` builds none and counts those changes as unknown. `--refresh` rebuilds every checklist in the period.
- `--workspace` covers every workspace repo, `--repo ALIAS` one of them, and `--no-workspace` forces single-repo mode.

## How to read them

Every item says how it is known: `measured`, `inferred`, `declared` or `unresolved`.
Missing evidence is reported as unresolved, never as a low score or a pass. Assessment
items keep the OpenSSF Scorecard check shape, so existing tooling can read them. The
change report ends with a method appendix that states what git alone cannot attest,
such as a review that left no trace in the history.

The reports describe code and components. Owners and reviewers are counted, never
ranked or named, and local paths are removed. They are evidence for a review, not a
certification.

Sections and renderers are plugin seams, so an organisation can add its own section
or output format. Every section and option: the
[CLI reference](../reference/CLI_REFERENCE.md#repowise-report-assessment-path).
