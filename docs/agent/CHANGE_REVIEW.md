# Change review — the Python API for reviewing one change

Everything the MCP tools and the CLI know about a change is computed by
`repowise.core`, and that core is importable on its own. This page is for a
program that wants the whole review in one object rather than a rendered
response: a bot commenting on a pull request, a CI job gating a merge, an
editor extension, a service that never has a checkout to score from.

Two things make that practical:

- **No checkout required.** The engine reads revision content through a
  `RevisionSource`. There is a Git one and a byte-backed one, and the analysis
  cannot tell them apart.
- **No server.** Nothing here imports `repowise.server`, an MCP registry, a
  response budget, FastAPI or a database session. A test enforces it.

```python
from repowise.core.analysis.change_review import ChangeReviewRequest, ChangeReviewService
from repowise.core.analysis.change_health import GitRevisionSource

service = ChangeReviewService(GitRevisionSource("."), repo_path=".")
bundle = service.review(ChangeReviewRequest(revspec="HEAD"))

print(bundle.directive.status)    # review_required | review_recommended | ...
print(bundle.directive.headline)  # a plain sentence, no Markdown
for entry in bundle.manifest:
    print(entry.status, entry.path, entry.added_line_count)
```

---

## Reviewing a change you have no checkout of

This is the case a hosted consumer actually has: the bytes arrived from an API
or an artifact, and there is no repository on disk. Build a `RevisionPair`
describing the change, hand over both sides' content, and the rest is
identical — same analyzers, same verdict.

```python
from repowise.core.analysis.change_health import (
    FileChange,
    MappingRevisionSource,
    RevisionPair,
)
from repowise.core.analysis.changed_lines import parse_unified_diff
from repowise.core.analysis.change_review import ChangeReviewRequest, ChangeReviewService

# Whatever your source calls them. A deletion has no head_path; a rename has
# both, and they differ.
diffs = parse_unified_diff(unified_diff_text)
pair = RevisionPair(
    base_ref="main",
    head_ref="pull/42/head",
    base_sha=base_sha,
    head_sha=head_sha,
    working_tree=False,
    changes=[
        FileChange(
            head_path="app/service.py",
            base_path="app/service.py",
            status="modified",
            diff=diffs.get("app/service.py"),
        )
    ],
)

source = MappingRevisionSource(
    pair,
    base={"app/service.py": base_bytes},
    head={"app/service.py": head_bytes},
)

# No repo_path: there is no checkout, so the shape score is unsupported rather
# than wrong. Every lane that works from content still answers.
bundle = ChangeReviewService(source).review(ChangeReviewRequest())
```

`FileChange` carries a `diff_status` for providers that cannot always supply a
usable patch — set it to `truncated` for a size-capped API page and `binary`
for a blob. Without it, "no lines changed" and "we never saw the lines" look
identical downstream, which is the one thing this type exists to prevent.

---

## Evidence status: the part to read first

A lane that was never consulted and a lane that found nothing are different
claims, and this API never collapses them. Every lane in `bundle.lanes`
carries one of five states:

| State | Meaning | Reading it as "fine" would be |
|---|---|---|
| `available` | Computed. An empty population is a real answer. | correct |
| `partial` | Some of the requested scope was analysed. | wrong — the change is not cleared |
| `unavailable` | The data was required and could not be read. | **wrong — this is a failure** |
| `degraded` | Computation failed or fell back to something weaker. | wrong |
| `unsupported` | Nobody asked, or this provider cannot answer at all. | wrong — nothing was checked |

```python
for name in bundle.degraded_lanes:
    state = bundle.lane(name)
    print(f"{name}: {state.state} — {state.reason}")
```

The distinction that matters most in practice: **`unavailable` is not
`available` with an empty list.** A bug-fix record that could not be read must
never render the way "these files have never broken" renders.

---

## What a bundle carries

| Lane | Field | Notes |
|---|---|---|
| `manifest` | `bundle.manifest` | Every counted path, its status, diff reliability and added line spans. The one counted universe — the request's extension and exclude filters are applied here, once. |
| `risk` | `bundle.risk` | Diff shape ranked against the repository's recent commits. Needs a checkout or a pre-scored result. |
| `health` | `bundle.health` | What the change newly made worse, each finding naming its attribution basis. |
| `contracts` | `bundle.contracts` | Changed symbols whose callers sit outside the change. |
| `tests` | `bundle.tests` | Impacted tests, measured coverage kept distinct from graph-inferred candidates. |
| `prior_fixes` | via `bundle.risk` | The bug-fix history the risk walk already read. |
| `independent_changes` | `bundle.independent_changes` | When the diff is several changes the index does not connect. |
| `branch_overlap` | `bundle.branch_overlap` | Other open branches editing the same files. |

Populations are **uncapped**. Core returns everything and says how much there
is; deciding how many rows to show is a surface's job, not core's.

`bundle.directive` is the verdict, its reasons and its next actions, as data —
a status, a plain factual headline, and `ReviewAction`s with stable
fingerprints so a caller can tell a repeated action from a new one. It carries
no Markdown, no URLs and no tool-call syntax, because two surfaces that render
it differently still have to reach the same conclusion.

---

## Supplying evidence the service will not fetch

The service is synchronous and fetches nothing that needs a database session,
a network call or a snapshot artifact. A caller collects those and hands them
over; the service decides what they mean. A caller that collects nothing still
gets a bundle, with those lanes honestly marked `unsupported`.

```python
from repowise.core.analysis.change_review import ChangeReviewEvidence, ContractInputs

bundle = service.review(
    ChangeReviewRequest(revspec="HEAD"),
    evidence=ChangeReviewEvidence(
        # Run the comparison alongside your other work rather than losing that
        # concurrency to a synchronous lane.
        health=precomputed_delta,
        tests=await analyze_test_impact(session, repo_id, changed_paths),
        contracts=ContractInputs(
            graph=graph, base_parsed=base_parsed, head_parsed=head_parsed,
            # True when the base side came from the last indexed snapshot
            # rather than this change's real base.
            base_is_snapshot=True,
        ),
    ),
)
```

`skip={"risk"}` drops a lane you do not want the cost of. It is reported as
`unsupported`, never as empty.

---

## Serializing it

`bundle.as_dict()` is a stable, JSON-safe projection. It always emits every
lane name as a key, so a reader never has to infer "absent" from a missing
one, and it leads with `contract_version`:

```python
payload = bundle.as_dict()
assert payload["contract_version"] == 1
```

`CHANGE_REVIEW_CONTRACT_VERSION` is bumped when a serialized bundle stops
being readable by the previous reader. Adding a field does not bump it;
removing or renaming one, or changing what an existing field means, does.

---

## Related

- The same evidence rendered for an agent: [MCP_TOOLS.md](MCP_TOOLS.md)
- `repowise risk` for the terminal, which reports the change-shape score and
  the independent-change split.
