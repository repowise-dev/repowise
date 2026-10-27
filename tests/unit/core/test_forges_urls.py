"""Forge web URLs, labels and change-ref display."""

from __future__ import annotations

import pytest

from repowise.core.forges import ForgeKind, all_forges, get_forge, parse_remote

GH_REF = "https://github.com/org/repo.git"
GHE_REF = "https://ghe.corp.com/org/repo.git"
GL_REF = "git@gitlab.com:g/sub/proj.git"
AZ_REF = "https://dev.azure.com/org/My%20Project/_git/repo"
BB_REF = "git@bitbucket.org:ws/repo.git"
BBDC_REF = "https://bitbucket.corp.com/scm/PROJ/repo.git"
SHA = "0123456789abcdef0123456789abcdef01234567"

_HOSTS = {"ghe.corp.com": ForgeKind.GITHUB, "bitbucket.corp.com": ForgeKind.BITBUCKET}


def _ref(url: str):
    ref = parse_remote(url, hosts=_HOSTS)
    assert ref is not None
    return ref


@pytest.mark.parametrize(
    ("url", "commit", "change", "blob", "blob_line", "compare"),
    [
        (
            GH_REF,
            f"https://github.com/org/repo/commit/{SHA}",
            "https://github.com/org/repo/pull/12",
            "https://github.com/org/repo/blob/main/src/a%20b.py",
            "https://github.com/org/repo/blob/feat/x/src/a%20b.py#L7",
            "https://github.com/org/repo/compare/main...feat/x",
        ),
        (
            GHE_REF,
            f"https://ghe.corp.com/org/repo/commit/{SHA}",
            "https://ghe.corp.com/org/repo/pull/12",
            "https://ghe.corp.com/org/repo/blob/main/src/a%20b.py",
            "https://ghe.corp.com/org/repo/blob/feat/x/src/a%20b.py#L7",
            "https://ghe.corp.com/org/repo/compare/main...feat/x",
        ),
        (
            GL_REF,
            f"https://gitlab.com/g/sub/proj/-/commit/{SHA}",
            "https://gitlab.com/g/sub/proj/-/merge_requests/12",
            "https://gitlab.com/g/sub/proj/-/blob/main/src/a%20b.py",
            "https://gitlab.com/g/sub/proj/-/blob/feat/x/src/a%20b.py#L7",
            "https://gitlab.com/g/sub/proj/-/compare/main...feat/x",
        ),
        (
            AZ_REF,
            f"https://dev.azure.com/org/My%20Project/_git/repo/commit/{SHA}",
            "https://dev.azure.com/org/My%20Project/_git/repo/pullrequest/12",
            "https://dev.azure.com/org/My%20Project/_git/repo"
            "?path=/src/a%20b.py&version=GBmain&_a=contents",
            "https://dev.azure.com/org/My%20Project/_git/repo"
            "?path=/src/a%20b.py&version=GBfeat/x&line=7&lineEnd=8"
            "&lineStartColumn=1&lineEndColumn=1&lineStyle=plain&_a=contents",
            "https://dev.azure.com/org/My%20Project/_git/repo"
            "/branchCompare?baseVersion=GBmain&targetVersion=GBfeat/x",
        ),
        (
            BB_REF,
            f"https://bitbucket.org/ws/repo/commits/{SHA}",
            "https://bitbucket.org/ws/repo/pull-requests/12",
            "https://bitbucket.org/ws/repo/src/main/src/a%20b.py",
            "https://bitbucket.org/ws/repo/src/feat/x/src/a%20b.py#lines-7",
            "https://bitbucket.org/ws/repo/branches/compare/feat/x%0Dmain",
        ),
        (
            BBDC_REF,
            f"https://bitbucket.corp.com/projects/PROJ/repos/repo/commits/{SHA}",
            "https://bitbucket.corp.com/projects/PROJ/repos/repo/pull-requests/12/overview",
            "https://bitbucket.corp.com/projects/PROJ/repos/repo/browse/src/a%20b.py?at=main",
            "https://bitbucket.corp.com/projects/PROJ/repos/repo/browse/src/a%20b.py"
            "?at=feat%2Fx#7",
            "https://bitbucket.corp.com/projects/PROJ/repos/repo/compare/commits"
            "?sourceBranch=feat%2Fx&targetBranch=main",
        ),
    ],
)
def test_url_builders(
    url: str, commit: str, change: str, blob: str, blob_line: str, compare: str
) -> None:
    ref = _ref(url)
    forge = get_forge(ref.forge)
    assert forge.commit_url(ref, SHA) == commit
    assert forge.change_url(ref, 12) == change
    assert forge.blob_url(ref, "main", "src/a b.py") == blob
    assert forge.blob_url(ref, "feat/x", "src/a b.py", 7) == blob_line
    assert forge.compare_url(ref, "main", "feat/x") == compare


def test_azure_pins_a_commit_with_gc_and_a_branch_with_gb() -> None:
    ref = _ref(AZ_REF)
    url = get_forge(ForgeKind.AZURE).blob_url(ref, SHA[:12], "a.py")
    assert f"version=GC{SHA[:12]}" in url


def test_generic_builds_no_urls() -> None:
    ref = _ref("https://gitea.example.org/team/tool.git")
    forge = get_forge(ref.forge)
    assert ref.forge is ForgeKind.GENERIC
    assert forge.commit_url(ref, SHA) == ""
    assert forge.change_url(ref, 1) == ""
    assert forge.blob_url(ref, "main", "a.py", 3) == ""
    assert forge.compare_url(ref, "a", "b") == ""


@pytest.mark.parametrize(
    ("kind", "label", "term", "display"),
    [
        (ForgeKind.GITHUB, "GitHub", "pull request", "#12"),
        (ForgeKind.GITLAB, "GitLab", "merge request", "!12"),
        (ForgeKind.AZURE, "Azure DevOps", "pull request", "PR 12"),
        (ForgeKind.BITBUCKET, "Bitbucket", "pull request", "#12"),
        (ForgeKind.GENERIC, "Git", "pull request", "#12"),
    ],
)
def test_labels_and_change_display(kind: ForgeKind, label: str, term: str, display: str) -> None:
    forge = get_forge(kind)
    assert (forge.kind, forge.label, forge.change_term) == (kind, label, term)
    assert forge.format_change_ref(12) == display


def test_every_kind_is_registered() -> None:
    assert {f.kind for f in all_forges()} == set(ForgeKind)
    assert get_forge("gitlab") is get_forge(ForgeKind.GITLAB)
