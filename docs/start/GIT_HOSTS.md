# Git hosts

Repowise reads your repository from git, so indexing, the graph, health and
the MCP tools work the same on any host. What depends on the host is smaller:
recognising the remote URL, reading the CI job's target branch, finding the
pull or merge request number in commit history, telling bots from people, and
matching a webhook to a registered repository. This page says what works where.

<p>
  <strong>Hosts &nbsp;</strong>
  <img src="https://img.shields.io/badge/GitHub-181717?style=flat-square&logo=github&logoColor=white" alt="GitHub" />
  <img src="https://img.shields.io/badge/GitLab-FC6D26?style=flat-square&logo=gitlab&logoColor=white" alt="GitLab" />
  <img src="https://img.shields.io/badge/Azure%20DevOps-0078D7?style=flat-square" alt="Azure DevOps" />
  <img src="https://img.shields.io/badge/Bitbucket-0052CC?style=flat-square&logo=bitbucket&logoColor=white" alt="Bitbucket" />
  <img src="https://img.shields.io/badge/Any%20git%20host-F05032?style=flat-square&logo=git&logoColor=white" alt="Any git host" />
</p>
<p>
  <strong>CI &nbsp;</strong>
  <img src="https://img.shields.io/badge/GitHub%20Actions-2088FF?style=flat-square&logo=githubactions&logoColor=white" alt="GitHub Actions" />
  <img src="https://img.shields.io/badge/GitLab%20CI-FC6D26?style=flat-square&logo=gitlab&logoColor=white" alt="GitLab CI" />
  <img src="https://img.shields.io/badge/Azure%20Pipelines-2560E0?style=flat-square" alt="Azure Pipelines" />
  <img src="https://img.shields.io/badge/Bitbucket%20Pipelines-0052CC?style=flat-square&logo=bitbucket&logoColor=white" alt="Bitbucket Pipelines" />
  <img src="https://img.shields.io/badge/Jenkins-D24939?style=flat-square&logo=jenkins&logoColor=white" alt="Jenkins" />
</p>

## Support

| Host | Remote recognised | CI detected | PR / MR numbers from history | Webhook auto-sync |
|------|-------------------|-------------|------------------------------|-------------------|
| GitHub | Yes | GitHub Actions | Yes | `/api/webhooks/github` |
| GitHub Enterprise | With a [host override](#self-managed-hosts) | GitHub Actions | Yes | `/api/webhooks/github` |
| GitLab.com | Yes | GitLab CI | Yes | `/api/webhooks/gitlab` |
| Self-managed GitLab | Yes when a host label is `gitlab` (`gitlab.corp.com`), else with an override | GitLab CI | Yes | `/api/webhooks/gitlab` |
| Azure DevOps (`dev.azure.com`, `ssh.dev.azure.com`, `*.visualstudio.com`) | Yes, all three URL forms as one repo | Azure Pipelines | Yes | No |
| Bitbucket Cloud | Yes | Bitbucket Pipelines | Yes | No |
| Bitbucket Data Center | With an override | Jenkins or another [listed CI](#ci-systems) | Yes | No |
| Other git hosts (Gitea, cgit, plain ssh) | As a generic host | Any [listed CI](#ci-systems) | Yes | No |

Azure DevOps Server needs an override too. Bot accounts and noreply addresses
from every host are recognised on every repository, since history can predate
a move between hosts. Remote URLs shown in the dashboard and returned by the
server have their credentials removed.

## Self-managed hosts

Repowise never probes a host over the network, so a self-managed host with a
neutral name (`git.corp.com`) reads as a generic host until you name its forge.
Add the mapping to `.repowise/config.yaml`:

```yaml
forges:
  hosts:
    git.corp.com: gitlab
    ghe.corp.com: github
    tfs.corp.com: azure
    bitbucket.corp.com: bitbucket
```

`.repowise/` is local and gitignored, so for CI runners and teammates set it in
the environment instead:

```bash
REPOWISE_FORGE_HOSTS="git.corp.com=gitlab,tfs.corp.com=azure"
```

Values are `github`, `gitlab`, `azure`, `bitbucket` or `generic`; an unknown
value is ignored. Keys match on the host alone, so a scheme or port in the key
does no harm. The environment variable wins over the config file. When the
remote's host is still unknown, the CI running the job decides: a GitLab runner
(`GITLAB_CI`) marks the repository as GitLab.

## CI systems

| CI | Detected by | Base branch read from | Change number read from |
|----|-------------|-----------------------|-------------------------|
| GitHub Actions | `GITHUB_ACTIONS` | `GITHUB_BASE_REF` | `GITHUB_REF` (`refs/pull/N/merge`) |
| GitLab CI | `GITLAB_CI` | `CI_MERGE_REQUEST_TARGET_BRANCH_NAME` | `CI_MERGE_REQUEST_IID` |
| Azure Pipelines | `TF_BUILD` or `SYSTEM_TEAMFOUNDATIONCOLLECTIONURI` | `SYSTEM_PULLREQUEST_TARGETBRANCHNAME`, else `SYSTEM_PULLREQUEST_TARGETBRANCH` | `SYSTEM_PULLREQUEST_PULLREQUESTNUMBER`, else `SYSTEM_PULLREQUEST_PULLREQUESTID` |
| Bitbucket Pipelines | `BITBUCKET_BUILD_NUMBER` | `BITBUCKET_PR_DESTINATION_BRANCH` | `BITBUCKET_PR_ID` |
| Jenkins | `JENKINS_URL` | `CHANGE_TARGET` (multibranch) | `CHANGE_ID` |
| Buildkite, TeamCity, any CI setting `CI` | `BUILDKITE`, `TEAMCITY_VERSION`, `CI` (known as CI only) | none: pass a range | none |

The CI gates diff `origin/<base branch>...HEAD` when you pass no range, with
`refs/heads/` removed from the branch. Azure Pipelines building a repository
hosted elsewhere reads `BUILD_REPOSITORY_PROVIDER` to tell which host it is.
Setup for each gate is in [CI.md](CI.md).

## What repowise reads from commit history

A commit gets a PR or MR number only when its own merge or squash message says
it is that change:

| Host | Forms that count |
|------|------------------|
| GitHub and generic hosts | Subject `Merge pull request #12 ...`; a subject ending `(#12)` or `(gh-12)` |
| GitLab | Body trailer `See merge request group/proj!12` (or its URL form); on GitLab repos, a subject ending `(!12)` |
| Azure DevOps | Subject `Merged PR 12: ...` or `Merge pull request 12 from ...` |
| Bitbucket | A subject ending `(pull request #12)`; subject `Merge pull request #12 in PROJ/repo ...` |

Every host's forms are read on every repository, the repository's own host
first. A bare `#12` never sets a number: it names an issue as often as a pull
request, and a wrong number is worse than none. Azure Boards links (`AB#12`)
and links to a pull request name some other change, so they do not count
either. A `revert:` commit that names a change (`#12`, a pull URL or `!12`) is
linked to it only when exactly one commit's merge message is that change.

Authors fold and bots drop out the same way on every host:

- GitHub noreply addresses (`123+login@users.noreply.github.com` and
  `login@users.noreply.github.com`) fold to one person. GitLab noreply
  addresses keep their account id and stay as written.
- Bots: names or emails with `[bot]`, names ending in `-bot`, `_bot` or ` bot`, common services
  (Dependabot, Renovate, Snyk, Codecov, Mergify, pre-commit.ci and others),
  `github-actions`, GitLab project and group access-token bots and
  `Ghost User`, Azure `... Build Service (org)` accounts and
  `bitbucket-pipelines`.

## Upgrading

The first `repowise update` after upgrading to a release that changes these
history rules re-reads the full git history once, so change numbers and
authors are rewritten for every file, not only the changed ones. It
regenerates no pages and makes no model calls. Later updates are incremental
again.

## See also

- [CI.md](CI.md) for the gates and the GitHub Action and GitLab templates
- [AUTO_SYNC.md](../scale/AUTO_SYNC.md) for webhook setup
- [SECURITY.md](../layers/SECURITY.md) for the `gitlab_token`,
  `azure_devops_pat` and `git_url_credentials` secret kinds
- [forges.md](../architecture/forges.md) for how host support is built and
  how to add a host
