"""What each CI's env vars say about the run: one table row per build shape."""

from __future__ import annotations

import pytest

from repowise.core.forges import ForgeKind
from repowise.core.forges.ci import CiContext, detect_ci

SHA = "a" * 40
BASE = "b" * 40

GH = ForgeKind.GITHUB
GL = ForgeKind.GITLAB
AZ = ForgeKind.AZURE
BB = ForgeKind.BITBUCKET


def ctx(ci, forge, branch=None, base=None, head=None, number=None, url=None) -> CiContext:
    return CiContext(ci, forge, branch, base, head, number, url)


CASES = {
    # -- GitHub Actions --------------------------------------------------
    "github pr": (
        {
            "GITHUB_ACTIONS": "true",
            "GITHUB_BASE_REF": "main",
            "GITHUB_HEAD_REF": "feat",
            "GITHUB_SHA": SHA,
            "GITHUB_REF": "refs/pull/42/merge",
            "GITHUB_SERVER_URL": "https://github.com",
            "GITHUB_REPOSITORY": "o/r",
        },
        ctx("github_actions", GH, "main", None, SHA, 42, "https://github.com/o/r"),
    ),
    "github push": (
        {"GITHUB_ACTIONS": "true", "GITHUB_BASE_REF": "", "GITHUB_REF": "refs/heads/main"},
        ctx("github_actions", GH),
    ),
    "github enterprise url with a trailing slash": (
        {
            "GITHUB_ACTIONS": "true",
            "GITHUB_SERVER_URL": "https://ghe.corp.com/",
            "GITHUB_REPOSITORY": "o/r",
        },
        ctx("github_actions", GH, url="https://ghe.corp.com/o/r"),
    ),
    "github half a repo url": (
        {"GITHUB_ACTIONS": "true", "GITHUB_REPOSITORY": "o/r"},
        ctx("github_actions", GH),
    ),
    "github off": ({"GITHUB_ACTIONS": "false", "GITHUB_BASE_REF": "main"}, ctx(None, None)),
    # -- GitLab CI -------------------------------------------------------
    "gitlab mr": (
        {
            "GITLAB_CI": "true",
            "CI_MERGE_REQUEST_TARGET_BRANCH_NAME": "develop",
            "CI_MERGE_REQUEST_DIFF_BASE_SHA": BASE,
            "CI_COMMIT_SHA": SHA,
            "CI_MERGE_REQUEST_IID": "7",
            "CI_PROJECT_URL": "https://gitlab.corp.com/g/sub/p",
        },
        ctx("gitlab_ci", GL, "develop", BASE, SHA, 7, "https://gitlab.corp.com/g/sub/p"),
    ),
    "gitlab push": (
        {"GITLAB_CI": "true", "CI_COMMIT_SHA": SHA},
        ctx("gitlab_ci", GL, head=SHA),
    ),
    "gitlab self-managed on a neutral host is still gitlab": (
        {"GITLAB_CI": "true", "CI_PROJECT_URL": "https://git.corp.com/g/p"},
        ctx("gitlab_ci", GL, url="https://git.corp.com/g/p"),
    ),
    "gitlab malformed iid and sha": (
        {"GITLAB_CI": "true", "CI_MERGE_REQUEST_IID": "7a", "CI_COMMIT_SHA": "not-a-sha"},
        ctx("gitlab_ci", GL),
    ),
    # -- Azure Pipelines -------------------------------------------------
    "azure repos pr": (
        {
            "TF_BUILD": "True",
            "SYSTEM_PULLREQUEST_TARGETBRANCH": "refs/heads/main",
            "SYSTEM_PULLREQUEST_PULLREQUESTID": "17",
            "BUILD_SOURCEVERSION": SHA,
            "BUILD_REPOSITORY_URI": "https://org@dev.azure.com/org/proj/_git/repo",
            "BUILD_REPOSITORY_PROVIDER": "TfsGit",
        },
        ctx("azure_pipelines", AZ, "main", None, SHA, 17, "https://dev.azure.com/org/proj/_git/repo"),
    ),
    "azure target branch name wins over the ref": (
        {
            "TF_BUILD": "True",
            "SYSTEM_PULLREQUEST_TARGETBRANCHNAME": "release/1.0",
            "SYSTEM_PULLREQUEST_TARGETBRANCH": "refs/heads/release/1.0",
        },
        ctx("azure_pipelines", None, "release/1.0"),
    ),
    "azure building a github repo": (
        {
            "TF_BUILD": "True",
            "SYSTEM_PULLREQUEST_TARGETBRANCH": "main",
            "SYSTEM_PULLREQUEST_PULLREQUESTID": "1234567890",
            "SYSTEM_PULLREQUEST_PULLREQUESTNUMBER": "12",
            "BUILD_REPOSITORY_URI": "https://github.com/o/r",
            "BUILD_REPOSITORY_PROVIDER": "GitHub",
        },
        ctx("azure_pipelines", GH, "main", number=12, url="https://github.com/o/r"),
    ),
    "azure github internal id alone is not a number": (
        {"TF_BUILD": "True", "SYSTEM_PULLREQUEST_PULLREQUESTID": "1234567890"},
        ctx("azure_pipelines", None),
    ),
    "azure bitbucket provider": (
        {"TF_BUILD": "True", "BUILD_REPOSITORY_PROVIDER": "Bitbucket"},
        ctx("azure_pipelines", BB),
    ),
    "azure unknown provider falls back to the url": (
        {
            "TF_BUILD": "True",
            "BUILD_REPOSITORY_PROVIDER": "Git",
            "BUILD_REPOSITORY_URI": "https://user:tok@gitlab.com/g/p.git",
        },
        ctx("azure_pipelines", GL, url="https://gitlab.com/g/p.git"),
    ),
    "azure server via collection uri, push build": (
        {
            "SYSTEM_TEAMFOUNDATIONCOLLECTIONURI": "https://tfs.corp/tfs/c/",
            "BUILD_SOURCEVERSION": SHA,
        },
        ctx("azure_pipelines", None, head=SHA),
    ),
    "azure bare refs/heads/ is no branch": (
        {"TF_BUILD": "True", "SYSTEM_PULLREQUEST_TARGETBRANCH": "refs/heads/"},
        ctx("azure_pipelines", None),
    ),
    # -- Bitbucket Pipelines ---------------------------------------------
    "bitbucket pr": (
        {
            "BITBUCKET_BUILD_NUMBER": "9",
            "BITBUCKET_PR_DESTINATION_BRANCH": "master",
            "BITBUCKET_COMMIT": SHA[:12],
            "BITBUCKET_PR_ID": "5",
            "BITBUCKET_GIT_HTTP_ORIGIN": "http://bitbucket.org/ws/repo",
        },
        ctx("bitbucket_pipelines", BB, "master", None, SHA[:12], 5, "http://bitbucket.org/ws/repo"),
    ),
    "bitbucket push": (
        {"BITBUCKET_BUILD_NUMBER": "9", "BITBUCKET_PR_ID": ""},
        ctx("bitbucket_pipelines", BB),
    ),
    # -- Jenkins ---------------------------------------------------------
    "jenkins pr": (
        {
            "JENKINS_URL": "https://ci.corp/",
            "CHANGE_TARGET": "main",
            "CHANGE_ID": "88",
            "GIT_COMMIT": SHA,
            "GIT_URL": "https://bot:secret@github.com/o/r.git",
        },
        ctx("jenkins", GH, "main", None, SHA, 88, "https://github.com/o/r.git"),
    ),
    "jenkins non-numeric change id, ssh remote": (
        {"JENKINS_URL": "https://ci.corp/", "CHANGE_ID": "PR-3", "GIT_URL": "git@gitlab.com:g/p.git"},
        ctx("jenkins", GL, url="git@gitlab.com:g/p.git"),
    ),
    "jenkins with no remote has no forge": (
        {"JENKINS_URL": "https://ci.corp/"},
        ctx("jenkins", None),
    ),
    "jenkins forge override from env": (
        {
            "JENKINS_URL": "https://ci.corp/",
            "GIT_URL": "https://git.corp.com/g/p.git",
            "REPOWISE_FORGE_HOSTS": "git.corp.com=gitlab",
        },
        ctx("jenkins", GL, url="https://git.corp.com/g/p.git"),
    ),
    # -- none --------------------------------------------------------------
    "no ci": ({}, ctx(None, None)),
    "plain CI is no known system": ({"CI": "true"}, ctx(None, None)),
}


@pytest.mark.parametrize(("env", "expected"), list(CASES.values()), ids=list(CASES))
def test_detect_ci(env: dict[str, str], expected: CiContext) -> None:
    assert detect_ci(env) == expected


@pytest.mark.parametrize(
    ("raw", "number"),
    [
        ("1", 1),
        ("999999999", 999999999),
        ("1000000000", None),
        ("0", None),
        ("", None),
        ("-3", None),
        ("+3", None),
        (" 12 ", 12),
        ("refs/pull/9/head", 9),
        ("refs/pull/x/merge", None),
        ("١٢", None),  # Arabic-Indic digits are digits to int(), not to a PR number
    ],
)
def test_change_numbers_are_bounded_digits(raw: str, number: int | None) -> None:
    env = {"GITLAB_CI": "true", "CI_MERGE_REQUEST_IID": raw}
    assert detect_ci(env).change_number == number


def test_reads_os_environ_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in ("GITHUB_ACTIONS", "GITLAB_CI", "TF_BUILD", "SYSTEM_TEAMFOUNDATIONCOLLECTIONURI"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("BITBUCKET_BUILD_NUMBER", "1")
    assert detect_ci().ci == "bitbucket_pipelines"
