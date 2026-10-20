"""Declared ownership: who a repository says owns what.

A :class:`TeamDirectory` returns :class:`DeclaredOwnership`: the CODEOWNERS
rules in force and the teams the repository declares. The local default,
``local``, reads only files in the repository:

* CODEOWNERS (:mod:`.codeowners`; GitHub and GitLab, sections included);
* Backstage Group entities in the component catalog files
  (``.repowise/components.yaml``, ``catalog-info.yaml``): names only, their
  ``spec.members`` is never read;
* ``.repowise/teams.yaml``: Backstage Group entities whose membership is
  read, only to tell whether a team changed a component recently. Members
  never appear in any output::

      apiVersion: backstage.io/v1alpha1
      kind: Group
      metadata:
        name: payments
        annotations:
          repowise.dev/member-emails: "ana@acme.com, li@acme.com"
      spec:
        type: team
        children: []
        members: [ana-acme, li-acme]   # forge logins

A plugin registers another directory (SCIM, forge teams) under
``SeamRegistry("team-directory")``, selected with ``REPOWISE_SEAM_TEAM_DIRECTORY``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import cached_property
from pathlib import Path
from typing import Any, Protocol

import yaml

from repowise.core.ownership.codeowners import (
    CodeownersMatcher,
    CodeownersRule,
    parse_codeowners,
    read_codeowners,
)
from repowise.core.registry.plugins import SeamRegistry

TEAMS_FILE = ".repowise/teams.yaml"
MEMBER_EMAILS_ANNOTATION = "repowise.dev/member-emails"
_API_VERSION = "backstage.io/v1alpha1"
_MAX_TEAMS_BYTES = 1_000_000


@dataclass(frozen=True)
class Team:
    name: str
    #: Forge logins and git author emails, lower-cased. Used to match commits
    #: to the team; never output.
    logins: tuple[str, ...] = ()
    emails: tuple[str, ...] = ()

    @property
    def has_members(self) -> bool:
        return bool(self.logins or self.emails)


@dataclass(frozen=True)
class OwnerRef:
    """A declared owner: a team, or an individual (a login or an email)."""

    #: "unresolved" is a bare ``@name`` that may be a person or a GitLab group.
    kind: str  # "team" | "individual" | "unresolved"
    #: The team name, or the individual's login or email (lower-cased).
    name: str
    team: Team | None = None


@dataclass
class DeclaredOwnership:
    teams: dict[str, Team] = field(default_factory=dict)  # by lower-cased name
    codeowners_file: str | None = None
    rules: list[CodeownersRule] = field(default_factory=list)
    issues: list[str] = field(default_factory=list)

    @cached_property
    def _matcher(self) -> CodeownersMatcher:
        return CodeownersMatcher(self.rules)

    def codeowners_of(self, path: str) -> tuple[str, ...]:
        return self._matcher.owners(path)

    def from_codeowners(self, owner: str) -> OwnerRef:
        """``@org/team`` is a team (named by its last segment) and an email an
        individual. A bare ``@name`` is a declared team of that name, else an
        individual in ``.github/CODEOWNERS`` (GitHub has no bare team handles),
        else unresolved: on GitLab it may name a group."""
        token = owner.strip()
        if not token.startswith("@"):
            return OwnerRef("individual", token.lower())
        handle = token[1:]
        if "/" in handle:
            return self._team(handle.rsplit("/", 1)[-1])
        team = self.teams.get(handle.lower())
        if team is not None:
            return OwnerRef("team", team.name, team)
        if self.codeowners_file == ".github/CODEOWNERS":
            return OwnerRef("individual", handle.lower())
        return OwnerRef("unresolved", handle.lower())

    def from_backstage(self, owner: str) -> OwnerRef:
        """A Backstage owner ref; without a kind it names a Group, as in Backstage."""
        kind, sep, rest = owner.strip().partition(":")
        ref = rest if sep else owner.strip()
        name = ref.rsplit("/", 1)[-1]
        if sep and kind.lower() == "user":
            return OwnerRef("individual", name.lower())
        return self._team(name)

    def _team(self, name: str) -> OwnerRef:
        team = self.teams.get(name.lower())
        return OwnerRef("team", team.name if team else name, team)


class TeamDirectory(Protocol):
    def load(self, repo_root: Path) -> DeclaredOwnership:
        """Declared ownership for the repository at *repo_root*."""
        ...


class LocalTeamDirectory:
    """CODEOWNERS, catalog Groups and ``.repowise/teams.yaml``; files only."""

    def load(self, repo_root: Path) -> DeclaredOwnership:
        from repowise.core.components.catalog import read_catalog

        out = DeclaredOwnership()
        found = read_codeowners(repo_root)
        if found is not None:
            out.codeowners_file, text = found
            out.rules = parse_codeowners(text)
        for group in read_catalog(repo_root).groups:
            out.teams.setdefault(group.name.lower(), Team(group.name))
        for team in _read_teams(Path(repo_root) / TEAMS_FILE, out.issues):
            out.teams[team.name.lower()] = team  # membership comes from teams.yaml
        return out


def _read_teams(path: Path, issues: list[str]) -> list[Team]:
    if not path.is_file():
        return []
    try:
        if path.stat().st_size > _MAX_TEAMS_BYTES:
            issues.append(f"{TEAMS_FILE}: larger than {_MAX_TEAMS_BYTES} bytes; not read")
            return []
        docs = [d for d in yaml.safe_load_all(path.read_text(encoding="utf-8")) if d is not None]
    except yaml.YAMLError as exc:
        # A fixed message: PyYAML quotes the source, which holds member emails.
        mark = getattr(exc, "problem_mark", None)
        where = f" near line {mark.line + 1}" if mark is not None else ""
        issues.append(f"{TEAMS_FILE}: invalid YAML{where}; not read")
        return []
    except (OSError, UnicodeDecodeError):
        issues.append(f"{TEAMS_FILE}: cannot be read as UTF-8 text; not read")
        return []
    teams: list[Team] = []
    for doc in docs:
        team = _team_entity(doc)
        if team is None:
            issues.append(f"{TEAMS_FILE}: a document is not a {_API_VERSION} Group with a name")
        else:
            teams.append(team)
    return teams


def _team_entity(doc: Any) -> Team | None:
    if not isinstance(doc, dict) or doc.get("kind") != "Group":
        return None
    if doc.get("apiVersion") != _API_VERSION:
        return None
    metadata = doc.get("metadata") if isinstance(doc.get("metadata"), dict) else {}
    name = metadata.get("name")
    if not isinstance(name, str) or not name.strip():
        return None
    spec = doc.get("spec") if isinstance(doc.get("spec"), dict) else {}
    members = spec.get("members") if isinstance(spec.get("members"), list) else []
    # A Backstage user ref (``user:default/ana``) names the login ``ana``.
    logins = [str(m).rsplit("/", 1)[-1].split(":")[-1].lstrip("@") for m in members]
    annotations = metadata.get("annotations")
    raw = annotations.get(MEMBER_EMAILS_ANNOTATION) if isinstance(annotations, dict) else None
    emails = re.split(r"[,\s]+", str(raw or ""))
    return Team(
        name.strip(),
        tuple(dict.fromkeys(m.strip().lower() for m in logins if m.strip())),
        tuple(dict.fromkeys(e.strip().lower() for e in emails if "@" in e)),
    )


team_directories: SeamRegistry[TeamDirectory] = SeamRegistry("team-directory")
team_directories.register("local", LocalTeamDirectory(), default=True)


__all__ = [
    "MEMBER_EMAILS_ANNOTATION",
    "TEAMS_FILE",
    "DeclaredOwnership",
    "LocalTeamDirectory",
    "OwnerRef",
    "Team",
    "TeamDirectory",
    "team_directories",
]
