# Forges: git host support

`packages/core/src/repowise/core/forges/` holds every fact repowise knows about
a git host (a forge): which hosts it claims, its URL shapes, its CI variables,
its merge-message conventions, its noreply addresses and bot accounts. Callers
ask this package and never match hosts themselves. The user-facing summary is
[GIT_HOSTS.md](../start/GIT_HOSTS.md).

## Module map

| File | Holds |
|------|-------|
| `base.py` | `ForgeKind`, `RemoteRef`, `RemoteParts`, the `Forge` protocol, `BaseForge` defaults, `CiSystem`, URL and ref helpers |
| `registry.py` | `register`, `get_forge`, `all_forges`, host resolution (override, then host claim, else generic), `forge_hosts` and `REPOWISE_FORGE_HOSTS` |
| `remote.py` | `parse_remote`, `canonical_key`, `strip_credentials`: pure string work on remote URLs |
| `detect.py` | `read_remote_url` (reads `.git/config`, follows worktrees) and `detect_forge` (remote, then CI env) |
| `changes.py` | `change_number` (the change a commit is) and `change_refs` (every change it mentions) |
| `identity.py` | `canonical_email`, `noreply_login`, `is_bot`, plus the bot names shared by every host |
| `ci.py` | `CiContext`, `detect_ci`, and CIs that belong to no forge (Jenkins) |
| `github.py`, `gitlab.py`, `azure.py`, `bitbucket.py` | One forge each: host claims, path parsing, URL builders, `CiSystem`, merge forms, identity rules |
| `generic.py` | The fallback: parses any `host/path`, builds no URLs, reads GitHub-style messages |

Users: the webhook lookup (`server/routers/webhooks.py`), `git_refs.remote_name`,
the overview remote and repository responses (`strip_credentials`),
`core/ci/base.py` (`CI_ENV_VARS`, `default_revspec`), the git indexer
(`change_number`), revert matching (`analysis/decisions/reverts.py`) and
`author_identity.py`.

## Invariants

- **Every forge fact lives in its forge's module.** A host name, URL shape,
  noreply domain, bot name or merge convention elsewhere is a bug. New host
  regexes go in this package, not in their callers.
- **Parsers do no I/O.** `remote.py`, `changes.py`, `identity.py` and `ci.py`
  read strings and env mappings only. `detect.py` reads `.git/config` directly
  instead of spawning git; `forge_hosts` reads the repo config.
- **Values are frozen, slotted dataclasses** (`RemoteRef`, `CiSystem`,
  `CiContext` and every forge). Registration replaces a forge; nothing mutates one.
- **Canonical keys** drop the scheme, userinfo, port, query, `.git`, trailing
  slashes and GitLab's `/-/` web routes, then lowercase the whole key, path
  included: GitHub and GitLab resolve repo paths case-insensitively, so a
  registered URL that differs from the webhook's only in case must still match.
  Azure's three host forms fold to `dev.azure.com/org/project/_git/repo`;
  Bitbucket Data Center keys as `host/PROJ/repo`.
- **Precision over recall.** A wrong change number is worse than none, so only
  a merge or squash form sets one; a bare `#N`, `AB#N` or a link never does.
  Every merge form needs a literal lead, exactly one capture group and no
  `IGNORECASE` flag (`lead_of` raises otherwise); write the number as
  `\d{1,9}`. For identity, a missed fold beats
  joining two people: only a forge's provable noreply form folds, and a bot
  name matches as an explicit marker or a full service name, never a substring.
- **Identity reads every forge.** An address keeps its minting forge's shape
  after a move, so `normalize_identity` gives the same answer on every forge.
- **History rules are versioned.** Changing a merge form or identity rule
  changes stored rows: bump `GIT_HISTORY_VERSION` in
  `ingestion/git_indexer/_constants.py` so the next update re-reads the full
  history once.

## Adding a forge

1. Add a member to `ForgeKind` in `base.py`.
2. Create `forges/<name>.py` with a `@dataclass(frozen=True, slots=True)`
   subclass of `BaseForge`.
3. Declare host claims in `claims_host` (exact public hosts and aliases; a
   self-managed install is reached through `forges.hosts`). Claims must stay
   disjoint from every other forge's.
4. Implement `parse` (the forge's path shape, `None` when it does not fit) and,
   if clone and web URLs differ, `canonical_path`.
5. Implement the URL builders: `commit_url`, `change_url`, `blob_url`,
   `compare_url`.
6. Set `label` and `change_term`, and override `format_change_ref` if the
   display is not `#N`.
7. Give it a `CiSystem` (markers, base branch, head sha, change number, repo
   URL) if the forge runs its own CI.
8. Declare merge forms (`merge_subject_res`, `merge_body_res`,
   `native_merge_subject_res`, `merge_body_first`) and implement
   `parse_change_refs` for mentions.
9. Declare identity rules: `noreply_re` with a `login` group and
   `noreply_fold`, `bot_name_re`, `bot_email_re`.
10. Call `register(...)` at module bottom and import the module in
    `forges/__init__.py`.
11. Add rows to each table test under `tests/unit/core/`:
    `test_forges_remote.py` (parse, key, credentials), `test_forges_urls.py`
    (URLs, label, change display), `test_forges_change_refs.py`
    (`change_number`, `change_refs`), `test_forges_identity.py` (noreply, bots),
    `test_forges_ci.py` (CI env) and `test_forges_detect.py` (remote and CI
    detection).
12. If a merge form or identity rule changes stored history, bump
    `GIT_HISTORY_VERSION`.

## Adding a CI system that is not a forge

A CI that builds repos from any host (Jenkins) gets a `CiSystem` in `ci.py`
and an entry in `_STANDALONE`; standalone CIs are tried after the forges'
own. `CI_ENV_VARS` and `CI_BASE_VARS` in `core/ci/base.py` pick up its markers
and base-branch vars automatically. A CI that is detected by presence alone,
with nothing to read, goes in `_PRESENCE_ONLY` in `core/ci/base.py`. Add its
build shapes to `CASES` in `tests/unit/core/test_forges_ci.py`.
