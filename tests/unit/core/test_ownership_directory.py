"""The team-directory seam and its local default (CODEOWNERS, Backstage, teams.yaml)."""

from __future__ import annotations

from repowise.core.ownership.directory import (
    LocalTeamDirectory,
    OwnerRef,
    Team,
    team_directories,
)

_CATALOG_INFO = """\
apiVersion: backstage.io/v1alpha1
kind: Component
metadata:
  name: payments
  annotations:
    repowise.dev/paths: src/payments/**
    repowise.dev/criticality: critical
spec: {type: service, lifecycle: production, owner: group:default/payments-team}
---
apiVersion: backstage.io/v1alpha1
kind: Group
metadata: {name: payments-team}
spec:
  type: team
  children: []
  members: [should-not-be-read]
---
apiVersion: backstage.io/v1alpha1
kind: Group
metadata: {name: platform}
spec: {type: team, children: []}
"""

_TEAMS = """\
apiVersion: backstage.io/v1alpha1
kind: Group
metadata:
  name: platform
  annotations:
    repowise.dev/member-emails: "Ana@Acme.com, li@acme.com"
spec:
  type: team
  children: []
  members: [user:default/ana-gh, "@li-gh"]
---
kind: Group
metadata: {name: no-api-version}
"""


def _repo(tmp_path):
    (tmp_path / ".repowise").mkdir()
    (tmp_path / "catalog-info.yaml").write_text(_CATALOG_INFO, encoding="utf-8")
    (tmp_path / ".repowise" / "teams.yaml").write_text(_TEAMS, encoding="utf-8")
    (tmp_path / "CODEOWNERS").write_text("/src/ @acme/platform\n", encoding="utf-8")
    return tmp_path


def test_local_directory_reads_codeowners_groups_and_teams_file(tmp_path) -> None:
    declared = LocalTeamDirectory().load(_repo(tmp_path))
    assert declared.codeowners_file == "CODEOWNERS"
    assert declared.codeowners_of("src/a.py") == ("@acme/platform",)
    # Catalog Groups are names only: their spec.members is never read.
    assert declared.teams["payments-team"] == Team("payments-team")
    assert declared.teams["platform"] == Team(
        "platform", ("ana-gh", "li-gh"), ("ana@acme.com", "li@acme.com")
    )
    assert declared.issues == [
        ".repowise/teams.yaml: a document is not a backstage.io/v1alpha1 Group with a name"
    ]


def test_owner_refs_from_codeowners_and_backstage(tmp_path) -> None:
    declared = LocalTeamDirectory().load(_repo(tmp_path))
    platform = declared.teams["platform"]
    assert declared.from_codeowners("@acme/platform") == OwnerRef("team", "platform", platform)
    assert declared.from_codeowners("@gitlab-org/sub/platform").team is platform
    assert declared.from_codeowners("@acme/unknown") == OwnerRef("team", "unknown", None)
    # A bare handle is a team only when one is declared by that name; outside
    # .github/ it may be a GitLab group, so it stays unresolved.
    assert declared.from_codeowners("@platform").kind == "team"
    assert declared.from_codeowners("@Someone") == OwnerRef("unresolved", "someone")
    assert declared.from_codeowners("Dev@Example.com") == OwnerRef("individual", "dev@example.com")
    assert declared.from_backstage("group:default/payments-team").kind == "team"
    assert declared.from_backstage("platform").team is platform
    assert declared.from_backstage("user:default/jdoe") == OwnerRef("individual", "jdoe")


def test_seam_defaults_to_the_local_directory() -> None:
    assert team_directories.default == "local"
    assert isinstance(team_directories.get(), LocalTeamDirectory)


def test_nothing_declared_is_an_empty_directory(tmp_path) -> None:
    declared = LocalTeamDirectory().load(tmp_path)
    assert (declared.teams, declared.rules, declared.issues) == ({}, [], [])
    assert declared.codeowners_of("a.py") == ()


def test_bare_handle_is_an_individual_only_in_a_github_file(tmp_path) -> None:
    (tmp_path / ".github").mkdir()
    (tmp_path / ".github" / "CODEOWNERS").write_text("* @someone\n", encoding="utf-8")
    declared = LocalTeamDirectory().load(tmp_path)
    assert declared.from_codeowners("@someone") == OwnerRef("individual", "someone")


def test_gitlab_bare_group_handle_is_unresolved(tmp_path) -> None:
    (tmp_path / ".gitlab").mkdir()
    (tmp_path / ".gitlab" / "CODEOWNERS").write_text(
        "[Backend] @backend-group\n/src/\n", encoding="utf-8"
    )
    declared = LocalTeamDirectory().load(tmp_path)
    (owner,) = declared.codeowners_of("src/a.py")
    assert declared.from_codeowners(owner) == OwnerRef("unresolved", "backend-group")


def test_malformed_teams_file_never_echoes_its_content(tmp_path) -> None:
    (tmp_path / ".repowise").mkdir()
    (tmp_path / ".repowise" / "teams.yaml").write_text(
        "kind: Group\nmetadata:\n  annotations:\n"
        '    repowise.dev/member-emails: "secret.person@acme.com\n  bad: [unclosed\n',
        encoding="utf-8",
    )
    declared = LocalTeamDirectory().load(tmp_path)
    assert len(declared.issues) == 1
    assert declared.issues[0].startswith(".repowise/teams.yaml: invalid YAML")
    assert "secret" not in declared.issues[0] and "acme" not in declared.issues[0]
