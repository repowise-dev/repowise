# Ownership and Knowledge Risk

Repowise reads git history to tell you who owns each file and module, how many
people it would take to lose the knowledge behind a file (its bus factor), and
where that knowledge is already at risk: a file still being edited whose main
author has gone quiet, a file with many drive-by authors and no clear owner, or
a busy file with too many hands on it. It also suggests reviewers for a set of
changed files.

It needs no LLM key and makes no network calls. Everything is computed from
`git log` (and `git blame` in the standard index) during `repowise init` and
`repowise update`. It does not read code review data, chat, or issue trackers,
so "owner" means "wrote the most commits or lines", not "is responsible for".

This layer needs git history. In a directory that is not a git repository the
ownership fields stay empty and the detectors below stay silent. A shallow clone
only sees the commits it fetched, so owners and bus factors describe that window;
run `git fetch --unshallow` before indexing for the full picture.

## Quick start

```bash
repowise init                                  # ownership is computed during indexing
repowise context src/auth/session.py --include ownership
repowise risk --target src/auth/session.py     # owner, recent owner, bus factor
repowise health --file src/auth/session.py     # knowledge_loss and friends, if they fire
```

From an agent:

```text
get_context(targets=["src/auth/session.py"], include=["ownership"])
get_risk(targets=["src/auth/session.py"])
get_risk(changed_files=["src/api/routes.ts", "src/middleware/cors.ts"])
get_overview(include=["ownership"])
```

In the dashboard, open **Owners** for the contributor directory and per-person
profiles, and the **Impact** tab of **Code Health** for reviewer suggestions on a
set of files.

Sample `get_context` ownership block:

```json
"ownership": {
  "primary_owner": "Dana Lee",
  "owner_pct": 0.71,
  "owner_line_pct": 0.83,
  "contributor_count": 4,
  "bus_factor": 1,
  "recent_owner": "Sam Ortiz",
  "recent_owner_pct": 0.6
}
```

## Reading the results

### Per-file fields

| Field | Meaning |
|---|---|
| `primary_owner` | The file's owner. In the standard index this is the author of the most surviving lines by `git blame`; in a fast index (`--mode fast`, no blame) it is the author of the most commits |
| `owner_pct` | The primary owner's share of the file's commits (0-1). Can be empty when the blame owner made none of the indexed commits |
| `owner_line_pct` | The primary owner's share of current lines by blame (0-1). Empty without blame |
| `recent_owner`, `recent_owner_pct` | Who made the most commits to the file in the last 90 days, and their share of those commits. `get_context` only shows these when the recent owner differs from the primary owner |
| `contributor_count` | Distinct authors in the indexed history of the file |
| `bus_factor` | How many of the top authors it takes to cover 80% of the file's commits. 1 means one person wrote at least 80% of them. 0 means no history was indexed for the file, not "no risk" |

Commits that only move a file do not count as authoring it. Author names and
emails go through the repository's `.mailmap`, and GitHub `noreply` addresses
for one login fold into one person, so a contributor who committed from several
addresses is usually counted once. Each file keeps its top 50 authors.

### Module ownership and silos

Per module (top-level directory), the owner is the person who is primary owner of
the most files in it. A module is a **silo** when one person owns more than 80%
of its files. Per file, the dashboard and REST API mark a silo when the primary
owner holds more than 80% of the file's commits.

### Detectors

Three code-health detectors turn these fields into findings. They run with the
rest of [code health](CODE_HEALTH.md) and appear in its findings list.

| Detector | Fires when | Severity |
|---|---|---|
| `knowledge_loss` | The file is still changing (a commit in 90 days, or a hotspot; never a file marked stable), its bus factor is 1, and the primary owner is no longer the recent owner or the recent owner makes under 20% of recent commits | High on a hotspot; medium when both conditions hold; otherwise low |
| `ownership_risk` | The file has 5 or more commits and either 3 or more minor contributors (each under 5% of commits) or no author above 40% | Critical at 6+ minor contributors on a hotspot; high at 5+, or 3+ on a hotspot; medium at 3+; otherwise low |
| `developer_congestion` | 5 or more contributors, the file is in the top quarter of churn for this repository, and nobody holds half the commits | High at 10+ contributors and 20+ commits in 90 days; medium at 7+ contributors or 12+ commits; otherwise low |

A stable file whose author left never fires `knowledge_loss`: code nobody edits
is low risk, and the finding only matters while people are editing around the
lost author's intent.

On a small team (3 or fewer active contributors in 90 days), one owner per file
is the normal way of working. `knowledge_loss` and `ownership_risk` still report
the finding there, but cap it at low severity unless the file is a hotspot, and
add "informational: small team" to the reason.

### Repository-level numbers

| Number | Where | Meaning |
|---|---|---|
| Truck factor | Stats page | Fewest primary owners who together own more than half the owned files. Bot authors are left out |
| Single-owner files | Stats page | Files with bus factor 1 |
| Silo count | Stats page | Modules where one person owns more than 80% of files |
| `git_health.avg_bus_factor`, `git_health.files_with_bus_factor_1` | `get_overview` | Average bus factor and count of bus-factor-1 files |
| `knowledge_map.top_owners` | `get_overview(include=["ownership"])` | The three people owning the most files, by display name only |

### Contributor profiles

Each person in **Owners** has a profile: files and hotspots they own, modules
where they hold more than 80% of files, files they own with bus factor 1, dead
code in files they own, commits in the 90 days before the newest commit, and
the people they share files with most.

### Reviewer suggestions

Two surfaces suggest reviewers for a set of changed files, and they score
differently:

- **Dashboard and REST** (`/api/repos/<id>/reviewer-suggestions`): each author of
  a touched file scores their share of its commits, plus a recency term from the
  file's 90-day activity. Authors of each touched file's five strongest
  co-change partners score at half weight. Each suggestion lists the files it
  owns and the co-change files behind it.
- **`get_risk(changed_files=...)`**: `directive.recommended_reviewers` lists
  up to five primary owners of the affected files, ranked by how many of those
  files they own, then by average ownership share.

## Tuning and suppressing

The detectors use the code-health rules file, `.repowise/health-rules.json`:

```json
{
  "profile": "small-team",
  "disabled_biomarkers": ["developer_congestion"],
  "rules": [
    { "path": "vendor/**", "disabled_biomarkers": ["ownership_risk", "knowledge_loss"] }
  ]
}
```

- The `small-team` profile lowers all three detectors (and a few process
  signals) to low severity.
- `severity_overrides` sets a detector's label; an explicit entry wins over the
  profile.
- History depth: `repowise init --commit-limit N` sets how many commits per file
  are read (default 500, maximum 10000). `--follow-renames` follows a file
  across renames, so ownership survives a move. Both are saved to config.
- `--mode fast` skips blame. Owners then come from commit counts alone and
  `owner_line_pct` stays empty.

Full schema: [CONFIG.md](../reference/CONFIG.md#the-health-rulesjson-file).

## Accuracy and limits

- Ownership is authorship. Someone who reviews every change to a file but never
  commits to it does not appear.
- Squash merges credit the whole change to whoever is recorded as the commit
  author. Pair-programming credit via `Co-authored-by` trailers is not counted
  toward ownership.
- Identity merging is conservative: beyond `.mailmap` and GitHub `noreply`
  folding, two addresses are only merged on guarded evidence, so some people may
  still appear twice. A false merge is treated as worse than a split.
- In the health score's defect calibration, `ownership_risk` showed positive
  lift and carries a higher weight than the default. `developer_congestion` and
  `knowledge_loss` showed weak signal and deduct less than the default; they are
  kept as findings a team can act on. All three share the change-history
  category cap. Method: [BENCHMARKS.md](../BENCHMARKS.md).
- A contributor profile's 90-day commit count is measured back from the newest
  commit, and stays empty when the stored commits do not reach back that far.
- Truncated history (shallow clone, low `--commit-limit`) can make an early
  author vanish, which can both hide and invent a `knowledge_loss` finding.

## Where it shows up

| Surface | What you get |
|---|---|
| CLI | `repowise context --include ownership`, `repowise risk --target` (owner, recent owner, bus factor), `repowise health` findings |
| MCP | `get_context` `ownership` block, `get_risk` owner fields and PR-mode reviewers, `get_overview` ownership summary, `get_health` findings |
| Dashboard | **Owners** directory and profiles, **Stats** people numbers, **Code Health** findings and **Impact** reviewer suggestions |
| Editor | VS Code: file hover shows the owner and their commit share; the **Hotspots & Ownership** view lists module owners and flags silos, with bus factor in each hotspot's tooltip |

## Reference

| Command | Flag | Effect |
|---|---|---|
| `repowise context` | `--include ownership` | Add the ownership block to each target's card |
| `repowise risk` | `--target PATH` | Score a file's history, including owners and bus factor |
| `repowise risk` | `--changed-file PATH` | With `--target`: PR mode; `--format json` includes `recommended_reviewers` |
| `repowise health` | `--file PATH` | One file's findings, including the ownership detectors |
| `repowise init` | `--commit-limit N` | Commits read per file (default 500, max 10000) |
| `repowise init` | `--follow-renames` | Track files across renames |
| `repowise init` | `--mode fast` | Skip blame; commit-count ownership only |

MCP: [get_context](../agent/MCP_TOOLS.md#get_context),
[get_risk](../agent/MCP_TOOLS.md#get_risk),
[get_overview](../agent/MCP_TOOLS.md#get_overview).

## See also

- [Code health](CODE_HEALTH.md): how the ownership detectors feed the score.
- [Change risk](CHANGE_RISK.md): co-change pairs and reviewing a diff.
- [Graph](GRAPH.md): hidden coupling between files that change together.
- [Bug history](BUG_HISTORY.md): which files keep needing fixes.
- [Intelligence layers](INTELLIGENCE_LAYERS.md): how this layer fits with the others.
