"""Remote parsing, canonical keys and credential scrubbing across forges."""

from __future__ import annotations

import pytest

from repowise.core.forges import (
    ForgeKind,
    RemoteRef,
    canonical_key,
    parse_remote,
    strip_credentials,
)

GH, GL, AZ, BB, GEN = (
    ForgeKind.GITHUB,
    ForgeKind.GITLAB,
    ForgeKind.AZURE,
    ForgeKind.BITBUCKET,
    ForgeKind.GENERIC,
)

#: Self-managed hosts as a repo's config or REPOWISE_FORGE_HOSTS would map them.
HOSTS = {
    "ghe.corp.com": GH,
    "git.corp.com": GL,
    "tfs.corp": AZ,
    "bitbucket.corp.com": BB,
}

# (url, forge, host, namespace, repo, web_base, canonical_key, is_self_hosted)
MATRIX = [
    # GitHub
    ("https://github.com/Org/Repo.git", GH, "github.com", ("Org",), "Repo",
     "https://github.com/Org/Repo", "github.com/org/repo", False),
    ("git@github.com:Org/Repo.git", GH, "github.com", ("Org",), "Repo",
     "https://github.com/Org/Repo", "github.com/org/repo", False),
    ("ssh://git@github.com/org/repo", GH, "github.com", ("org",), "repo",
     "https://github.com/org/repo", "github.com/org/repo", False),
    ("ssh://git@ssh.github.com:443/org/repo.git", GH, "github.com", ("org",), "repo",
     "https://github.com/org/repo", "ssh.github.com/org/repo", False),
    ("https://x-access-token:ghp_secret@github.com/org/repo.git", GH, "github.com", ("org",),
     "repo", "https://github.com/org/repo", "github.com/org/repo", False),
    ("https://github.com/org/repo/", GH, "github.com", ("org",), "repo",
     "https://github.com/org/repo", "github.com/org/repo", False),
    ("github.com/org/repo", GH, "github.com", ("org",), "repo",
     "https://github.com/org/repo", "github.com/org/repo", False),
    ("git://github.com/org/repo.git", GH, "github.com", ("org",), "repo",
     "https://github.com/org/repo", "github.com/org/repo", False),
    # GitHub Enterprise Server: only with a hosts override.
    ("https://ghe.corp.com/team/svc.git", GH, "ghe.corp.com", ("team",), "svc",
     "https://ghe.corp.com/team/svc", "ghe.corp.com/team/svc", True),
    ("git@ghe.corp.com:team/svc.git", GH, "ghe.corp.com", ("team",), "svc",
     "https://ghe.corp.com/team/svc", "ghe.corp.com/team/svc", True),
    # GitLab
    ("https://gitlab.com/group/proj.git", GL, "gitlab.com", ("group",), "proj",
     "https://gitlab.com/group/proj", "gitlab.com/group/proj", False),
    ("git@gitlab.com:g/sub/sub2/proj.git", GL, "gitlab.com", ("g", "sub", "sub2"), "proj",
     "https://gitlab.com/g/sub/sub2/proj", "gitlab.com/g/sub/sub2/proj", False),
    ("https://gitlab.com/g/sub/proj/-/tree/main/src", GL, "gitlab.com", ("g", "sub"), "proj",
     "https://gitlab.com/g/sub/proj", "gitlab.com/g/sub/proj", False),
    ("https://gitlab.com/g/proj/-/merge_requests/12", GL, "gitlab.com", ("g",), "proj",
     "https://gitlab.com/g/proj", "gitlab.com/g/proj", False),
    ("https://oauth2:glpat-secret@gitlab.com/g/proj.git", GL, "gitlab.com", ("g",), "proj",
     "https://gitlab.com/g/proj", "gitlab.com/g/proj", False),
    ("https://user%40corp.com:tok@gitlab.com/g/proj.git", GL, "gitlab.com", ("g",), "proj",
     "https://gitlab.com/g/proj", "gitlab.com/g/proj", False),
    # Self-managed GitLab: by host name, or by override, with an ssh port.
    ("https://gitlab.example.com/group/sub/flask/", GL, "gitlab.example.com", ("group", "sub"),
     "flask", "https://gitlab.example.com/group/sub/flask",
     "gitlab.example.com/group/sub/flask", True),
    ("ssh://git@gitlab.example.com:2222/g/proj.git", GL, "gitlab.example.com", ("g",), "proj",
     "https://gitlab.example.com/g/proj", "gitlab.example.com/g/proj", True),
    ("ssh://git@git.corp.com:2222/g/sub/proj.git", GL, "git.corp.com", ("g", "sub"), "proj",
     "https://git.corp.com/g/sub/proj", "git.corp.com/g/sub/proj", True),
    ("https://git.corp.com:8443/g/proj.git", GL, "git.corp.com", ("g",), "proj",
     "https://git.corp.com:8443/g/proj", "git.corp.com/g/proj", True),
    ("http://git.corp.com/g/proj", GL, "git.corp.com", ("g",), "proj",
     "http://git.corp.com/g/proj", "git.corp.com/g/proj", True),
    # Azure DevOps Services: three host shapes, one key.
    ("https://org@dev.azure.com/org/project/_git/repo", AZ, "dev.azure.com", ("org", "project"),
     "repo", "https://dev.azure.com/org/project/_git/repo",
     "dev.azure.com/org/project/_git/repo", False),
    ("https://dev.azure.com/Org/Project/_git/Repo", AZ, "dev.azure.com", ("Org", "Project"),
     "Repo", "https://dev.azure.com/Org/Project/_git/Repo",
     "dev.azure.com/org/project/_git/repo", False),
    ("git@ssh.dev.azure.com:v3/org/project/repo", AZ, "dev.azure.com", ("org", "project"),
     "repo", "https://dev.azure.com/org/project/_git/repo",
     "dev.azure.com/org/project/_git/repo", False),
    ("ssh://git@ssh.dev.azure.com/v3/org/project/repo", AZ, "dev.azure.com",
     ("org", "project"), "repo", "https://dev.azure.com/org/project/_git/repo",
     "dev.azure.com/org/project/_git/repo", False),
    ("https://org.visualstudio.com/project/_git/repo", AZ, "dev.azure.com", ("org", "project"),
     "repo", "https://dev.azure.com/org/project/_git/repo",
     "dev.azure.com/org/project/_git/repo", False),
    ("https://org.visualstudio.com/DefaultCollection/project/_git/repo", AZ, "dev.azure.com",
     ("org", "project"), "repo", "https://dev.azure.com/org/project/_git/repo",
     "dev.azure.com/org/project/_git/repo", False),
    ("org@vs-ssh.visualstudio.com:v3/org/project/repo", AZ, "dev.azure.com",
     ("org", "project"), "repo", "https://dev.azure.com/org/project/_git/repo",
     "dev.azure.com/org/project/_git/repo", False),
    ("https://dev.azure.com/org/_git/repo", AZ, "dev.azure.com", ("org", "repo"), "repo",
     "https://dev.azure.com/org/repo/_git/repo", "dev.azure.com/org/repo/_git/repo", False),
    ("https://dev.azure.com/org/project/_git/repo/pullrequest/7", AZ, "dev.azure.com",
     ("org", "project"), "repo", "https://dev.azure.com/org/project/_git/repo",
     "dev.azure.com/org/project/_git/repo", False),
    ("https://user%40corp.com:pat@dev.azure.com/org/proj/_git/repo", AZ, "dev.azure.com",
     ("org", "proj"), "repo", "https://dev.azure.com/org/proj/_git/repo",
     "dev.azure.com/org/proj/_git/repo", False),
    # Names with spaces, escaped and raw, all on one key.
    ("https://org@dev.azure.com/org/My%20Project/_git/My%20Repo", AZ, "dev.azure.com",
     ("org", "My Project"), "My Repo", "https://dev.azure.com/org/My%20Project/_git/My%20Repo",
     "dev.azure.com/org/my%20project/_git/my%20repo", False),
    ("https://dev.azure.com/org/My Project/_git/My Repo", AZ, "dev.azure.com",
     ("org", "My Project"), "My Repo", "https://dev.azure.com/org/My%20Project/_git/My%20Repo",
     "dev.azure.com/org/my%20project/_git/my%20repo", False),
    ("git@ssh.dev.azure.com:v3/org/My%20Project/My%20Repo", AZ, "dev.azure.com",
     ("org", "My Project"), "My Repo", "https://dev.azure.com/org/My%20Project/_git/My%20Repo",
     "dev.azure.com/org/my%20project/_git/my%20repo", False),
    # Azure DevOps Server: collection path kept, override needed.
    ("https://tfs.corp/tfs/DefaultCollection/Proj/_git/Repo", AZ, "tfs.corp",
     ("tfs", "DefaultCollection", "Proj"), "Repo",
     "https://tfs.corp/tfs/DefaultCollection/Proj/_git/Repo",
     "tfs.corp/tfs/defaultcollection/proj/_git/repo", True),
    ("ssh://tfs.corp:22/DefaultCollection/Proj/_git/Repo", AZ, "tfs.corp",
     ("DefaultCollection", "Proj"), "Repo", "https://tfs.corp/DefaultCollection/Proj/_git/Repo",
     "tfs.corp/defaultcollection/proj/_git/repo", True),
    # Bitbucket Cloud
    ("https://bitbucket.org/ws/repo.git", BB, "bitbucket.org", ("ws",), "repo",
     "https://bitbucket.org/ws/repo", "bitbucket.org/ws/repo", False),
    ("https://user@bitbucket.org/ws/repo.git", BB, "bitbucket.org", ("ws",), "repo",
     "https://bitbucket.org/ws/repo", "bitbucket.org/ws/repo", False),
    ("git@bitbucket.org:ws/repo.git", BB, "bitbucket.org", ("ws",), "repo",
     "https://bitbucket.org/ws/repo", "bitbucket.org/ws/repo", False),
    # Bitbucket Data Center: http under /scm, ssh on its own port, browse URLs.
    ("https://bitbucket.corp.com/scm/PROJ/repo.git", BB, "bitbucket.corp.com", ("PROJ",), "repo",
     "https://bitbucket.corp.com/projects/PROJ/repos/repo",
     "bitbucket.corp.com/proj/repo", True),
    ("https://bitbucket.corp.com/bb/scm/PROJ/repo.git", BB, "bitbucket.corp.com", ("PROJ",),
     "repo", "https://bitbucket.corp.com/bb/projects/PROJ/repos/repo",
     "bitbucket.corp.com/proj/repo", True),
    ("ssh://git@bitbucket.corp.com:7999/proj/repo.git", BB, "bitbucket.corp.com", ("PROJ",),
     "repo", "https://bitbucket.corp.com/projects/PROJ/repos/repo",
     "bitbucket.corp.com/proj/repo", True),
    ("https://bitbucket.corp.com/scm/~jdoe/repo.git", BB, "bitbucket.corp.com", ("~jdoe",),
     "repo", "https://bitbucket.corp.com/users/jdoe/repos/repo",
     "bitbucket.corp.com/~jdoe/repo", True),
    ("ssh://git@bitbucket.corp.com:7999/~jdoe/repo.git", BB, "bitbucket.corp.com", ("~jdoe",),
     "repo", "https://bitbucket.corp.com/users/jdoe/repos/repo",
     "bitbucket.corp.com/~jdoe/repo", True),
    ("https://bitbucket.corp.com/projects/PROJ/repos/repo/browse", BB, "bitbucket.corp.com",
     ("PROJ",), "repo", "https://bitbucket.corp.com/projects/PROJ/repos/repo",
     "bitbucket.corp.com/proj/repo", True),
    # Generic: unknown hosts, no override.
    ("https://gitea.example.org/team/tool.git", GEN, "gitea.example.org", ("team",), "tool",
     "https://gitea.example.org/team/tool", "gitea.example.org/team/tool", True),
    ("git@git.sr.ht:~user/proj", GEN, "git.sr.ht", ("~user",), "proj",
     "https://git.sr.ht/~user/proj", "git.sr.ht/~user/proj", True),
    ("ssh://git@[::1]:2222/srv/repo.git", GEN, "::1", ("srv",), "repo",
     "https://[::1]/srv/repo", "::1/srv/repo", True),
    ("git.lan:8443/g/p", GEN, "git.lan", ("g",), "p", "https://git.lan:8443/g/p",
     "git.lan/g/p", True),
]


@pytest.mark.parametrize(
    ("url", "forge", "host", "namespace", "repo", "web_base", "key", "self_hosted"), MATRIX
)
def test_parse_and_key(
    url: str,
    forge: ForgeKind,
    host: str,
    namespace: tuple[str, ...],
    repo: str,
    web_base: str,
    key: str,
    self_hosted: bool,
) -> None:
    assert parse_remote(url, hosts=HOSTS) == RemoteRef(
        forge, host, namespace, repo, web_base, self_hosted
    )
    assert canonical_key(url, hosts=HOSTS) == key


@pytest.mark.parametrize(
    "url",
    [
        "https://ghe.corp.com/team/svc.git",
        "ssh://git@git.corp.com:2222/g/proj.git",
        "https://tfs.corp/tfs/DefaultCollection/Proj/_git/Repo",
        "https://bitbucket.corp.com/scm/PROJ/repo.git",
    ],
)
def test_a_self_managed_host_is_generic_without_an_override(url: str) -> None:
    ref = parse_remote(url)
    assert ref is not None and ref.forge is GEN


@pytest.mark.parametrize(
    "urls",
    [
        (
            "https://bitbucket.corp.com/scm/PROJ/repo.git",
            "https://user:tok@bitbucket.corp.com/bb/scm/proj/repo.git",
            "ssh://git@bitbucket.corp.com:7999/proj/repo.git",
            "git@bitbucket.corp.com:PROJ/repo.git",
        ),
        (
            "https://org@dev.azure.com/org/p/_git/r",
            "git@ssh.dev.azure.com:v3/org/p/r",
            "https://org.visualstudio.com/DefaultCollection/p/_git/r",
        ),
    ],
)
def test_every_clone_url_of_one_repo_shares_a_key_and_a_name(urls: tuple[str, ...]) -> None:
    assert len({canonical_key(u, hosts=HOSTS) for u in urls}) == 1
    refs = {parse_remote(u, hosts=HOSTS) for u in urls}
    assert len({(r.namespace, r.repo) for r in refs if r}) == 1


def test_an_empty_repo_keys_like_the_webhook_did() -> None:
    assert canonical_key("https://github.com/o/.git") == "github.com/o"


@pytest.mark.parametrize(
    ("url", "key"),
    [
        # Without an override a Data Center host is generic and keeps its path.
        ("https://bitbucket.corp.com/scm/PROJ/repo.git", "bitbucket.corp.com/scm/proj/repo"),
        ("https://git.corp.com/scm/x/repo.git", "git.corp.com/scm/x/repo"),
        ("https://git.corp.com/g/scm/x/repo.git", "git.corp.com/g/scm/x/repo"),
        ("https://gitlab.com/scm/x/repo", "gitlab.com/scm/x/repo"),
        ("https://github.com/scm/x/repo.git", "github.com/scm/x/repo"),
    ],
)
def test_a_path_named_scm_is_only_folded_on_bitbucket(url: str, key: str) -> None:
    assert canonical_key(url) == key


@pytest.mark.parametrize(
    ("url", "key"),
    [
        ("https://a/g/p.git", "a/g/p"),
        ("ssh://git@b:2222/g/p.git", "b/g/p"),
    ],
)
def test_a_single_character_host_parses_outside_scp_form(url: str, key: str) -> None:
    """Only the scp form reads `c:path` as a drive letter, which is how git reads it."""
    assert canonical_key(url) == key


def test_an_override_accepts_a_plain_string_kind_and_any_key_case() -> None:
    ref = parse_remote("git@Git.Corp.com:g/p.git", hosts={"GIT.CORP.COM": "gitlab"})  # type: ignore[dict-item]
    assert ref is not None and ref.forge is GL


@pytest.mark.parametrize(
    "url",
    [
        "",
        "   ",
        "/srv/git/repo.git",
        "../sibling",
        "C:\\repos\\flask",
        "C:/repos/flask",
        "file:///srv/git/p.git",
        "ftp://host/x",
        "https://",
        "https://github.com/onlyowner",
        "https://dev.azure.com/org/project/repo",
        "git@ssh.dev.azure.com:org/project/repo",
        "https://github.com/o/.git",
        "git@gitlab.com:g/.git",
    ],
)
def test_non_remotes_and_misshapen_paths_parse_to_none(url: str) -> None:
    assert parse_remote(url) is None


@pytest.mark.parametrize(
    "url", ["", "  ", "/srv/git/repo.git", "C:\\repos\\flask", "file:///x", "https://"]
)
def test_canonical_key_is_none_for_a_non_remote(url: str) -> None:
    assert canonical_key(url) is None


@pytest.mark.parametrize(
    ("url", "legacy_key"),
    [
        ("https://github.com/Org/Repo.git", "github.com/org/repo"),
        ("https://github.com/Org/Repo/", "github.com/org/repo"),
        ("git@github.com:Org/Repo.git", "github.com/org/repo"),
        ("https://github.com/org/RepoWise", "github.com/org/repowise"),
        ("https://user:tok@github.com/o/r.git?x=1#f", "github.com/o/r"),
        ("ssh://git@git.corp:2222/g/p.git", "git.corp/g/p"),
        ("https://github.com/o/r/tree/main", "github.com/o/r/tree/main"),
        ("https://gitlab.example.com/a/b/c.git/", "gitlab.example.com/a/b/c"),
    ],
)
def test_non_azure_keys_match_the_webhook_key_they_replace(url: str, legacy_key: str) -> None:
    """Stored repos are matched by this key, so a changed shape stops a webhook syncing."""
    assert canonical_key(url) == legacy_key


# The same table as the server's own scrubber: the remote reaches the browser.
@pytest.mark.parametrize(
    ("remote", "expected"),
    [
        ("https://user:glpat-secret@gitlab.com/g/sub/p.git", "https://gitlab.com/g/sub/p.git"),
        ("https://ghp_secret@github.com/o/r.git", "https://github.com/o/r.git"),
        (
            "https://user%40corp.com:pat@dev.azure.com/org/proj/_git/repo",
            "https://dev.azure.com/org/proj/_git/repo",
        ),
        ("http://u:p@git.corp:8080/g/p", "http://git.corp:8080/g/p"),
        ("ssh://git:secret@git.corp:2222/g/p.git", "ssh://git@git.corp:2222/g/p.git"),
        ("ssh://git@github.com/o/r.git", "ssh://git@github.com/o/r.git"),
        ("git@github.com:o/r.git", "git@github.com:o/r.git"),
        ("https://github.com/o/r", "https://github.com/o/r"),
        ("HTTPS://tok@github.com/o/r", "HTTPS://github.com/o/r"),
        ("  https://ghp_tok@github.com/o/r  ", "https://github.com/o/r"),
        ("git+https://ghp_tok@github.com/o/r", "git+https://github.com/o/r"),
        ("ftp://tok@host/x", "ftp://host/x"),
        ("https://user:pa/ss@host/p.git", "https://host/p.git"),
        ("https://gitlab.com/g/p.git?private_token=abc#frag", "https://gitlab.com/g/p.git"),
        ("https://user:123/secret@host/repo.git", "https://host/repo.git"),
        ("file:///srv/git/p@1.git", "file:///srv/git/p@1.git"),
        ("https://host/org/repo@release.git", "https://host/org/repo@release.git"),
        ("https://git.example.com/org/repo@v1.git", "https://git.example.com/org/repo@v1.git"),
        ("git@host:org/repo@v1.git", "git@host:org/repo@v1.git"),
        ("host:org/repo@v1.git", "host:org/repo@v1.git"),
        ("ssh://ghp_tok@host/x", "ssh://ghp_tok@host/x"),
        ("user:pw@host:g/p.git", "user@host:g/p.git"),
        ("user:pa/ss@host:g/p.git", "user@host:g/p.git"),
        ("https://user:SE?CRET@host/r.git", "https://host/r.git"),
        ("https://user:SE#CRET@host/r.git", "https://host/r.git"),
        ("https://host:8080/p.git?x=a@b", "https://host:8080/p.git"),
        ("/srv/a:b@c/p.git", "/srv/a:b@c/p.git"),
        ("https://[::1]:8443/g/p", "https://[::1]:8443/g/p"),
        ("file:///srv/git/p.git", "file:///srv/git/p.git"),
        ("", ""),
    ],
)
def test_strip_credentials(remote: str, expected: str) -> None:
    assert strip_credentials(remote) == expected


def test_a_password_with_a_slash_never_reaches_the_parsed_host() -> None:
    ref = parse_remote("https://user:pa/ss@github.com/o/r.git")
    assert ref is not None and (ref.host, ref.repo) == ("github.com", "r")
