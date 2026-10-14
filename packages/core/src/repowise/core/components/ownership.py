"""Declared against observed ownership, per component.

Declared owners come from the :mod:`team-directory <repowise.core.ownership.directory>`
seam: the component's catalog ``spec.owner`` (all its files) and the
CODEOWNERS owners of its files (the files each one is assigned). Observed
activity is the indexed commits by people (automation left out) in the
:data:`ACTIVITY_WINDOW_DAYS` before the newest indexed commit.

A declared owner is active when someone it covers changed one of the files it
owns in that window. A team is matched through the members ``teams.yaml``
(or a plugin directory) declares; an individual through a git email, or a
login seen on a forge noreply address. An owner none of whose identities
appears anywhere in the indexed history is ``unknown``, never inactive.

Findings are about components and teams only. An individual owner is counted,
never named.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from repowise.core import forges
from repowise.core.analysis.owners import ACTIVITY_WINDOW_DAYS
from repowise.core.author_identity import IdentityResolver, build_identity_resolver
from repowise.core.components.catalog import Component
from repowise.core.ownership.directory import DeclaredOwnership, OwnerRef

#: Criticality levels the critical-component findings apply to.
CRITICAL_LEVELS = frozenset({"high", "critical"})


@dataclass
class Activity:
    """Who changed which file recently, as identity keys. Built by :func:`build_activity`."""

    resolve: IdentityResolver | None = None
    #: Every human identity in the indexed history.
    known: set[str] = field(default_factory=set)
    #: Login (lower-cased) to the identities seen committing as its noreply address.
    login_keys: dict[str, set[str]] = field(default_factory=dict)
    #: File to the human identities that changed it in the window.
    recent: dict[str, set[str]] = field(default_factory=dict)
    #: File to the commits that changed it in the window.
    recent_commits: dict[str, set[str]] = field(default_factory=dict)
    as_of: datetime | None = None

    @property
    def available(self) -> bool:
        return self.resolve is not None

    def keys_of(self, ref: OwnerRef) -> set[str] | None:
        """The identities *ref* covers that history knows; ``None`` when none is known."""
        if self.resolve is None or ref.kind == "unresolved":
            return None
        if ref.kind == "team":
            team = ref.team
            tokens = [*team.logins, *team.emails] if team is not None else []
        else:
            tokens = [ref.name]
        keys: set[str] = set()
        for token in tokens:
            if "@" in token:
                key = self.resolve(None, token)
                if key in self.known:
                    keys.add(key)
            else:
                keys |= self.login_keys.get(token, set())
        return keys or None


def build_activity(
    authors: Iterable[tuple[str | None, str | None]],
    recent_rows: Iterable[tuple[str, str, str | None, str | None]],
    as_of: datetime | None,
) -> Activity:
    """*authors*: every commit's ``(name, email)``; *recent_rows*: ``(file, sha,
    name, email)`` for each file a commit in the window changed."""
    pairs = list(authors)
    if not pairs:
        return Activity()
    resolve = build_identity_resolver(pairs)
    activity = Activity(resolve=resolve, as_of=as_of)
    for name, email in pairs:
        key = resolve(name, email)
        if resolve.is_bot(key) or not key:
            continue
        activity.known.add(key)
        login = forges.noreply_login(email)
        if login:
            activity.login_keys.setdefault(login[1].lower(), set()).add(key)
    for path, sha, name, email in recent_rows:
        key = resolve(name, email)
        if key in activity.known:
            activity.recent.setdefault(path, set()).add(key)
            activity.recent_commits.setdefault(path, set()).add(sha)
    return activity


def assess_component(
    comp: Component,
    files: list[str],
    declared: DeclaredOwnership,
    activity: Activity,
    bus_factor: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """The ``ownership`` block for one component: declared owners, recent
    activity and findings."""
    findings: list[dict[str, str]] = []
    critical = comp.criticality in CRITICAL_LEVELS
    out: dict[str, Any] = {"file_count": len(files)}
    if not files:
        if comp.declared:
            findings.append(
                _finding("catalog_entry_no_code", "Declared in the catalog; claims no files.")
            )
        out["findings"] = findings
        return out

    owned = _declared_files(comp, files, declared)
    teams: list[dict[str, Any]] = []
    individuals = {"count": 0, "no_recent_changes": 0, "unknown": 0}
    unresolved = 0
    states: list[str] = []
    for ref, paths in owned.items():
        state = _activity_state(ref, paths, activity)
        states.append(state)
        if ref.kind == "team":
            row: dict[str, Any] = {"team": ref.name, "files": len(paths), "activity": state}
            if ref.team is None:
                row["in_directory"] = False  # no Group or teams.yaml entry by this name
            teams.append(row)
            if state == "no_recent_changes":
                findings.append(
                    _finding(
                        "declared_owner_inactive",
                        f"Declared owner team {ref.name!r} has no changes to its "
                        f"{len(paths)} file(s) here in the last {ACTIVITY_WINDOW_DAYS} days.",
                    )
                )
        elif ref.kind == "unresolved":
            unresolved += 1
        else:
            individuals["count"] += 1
            if state in ("no_recent_changes", "unknown"):
                individuals[state] += 1
    if individuals["no_recent_changes"]:
        findings.append(
            _finding(
                "declared_owner_inactive",
                f"{individuals['no_recent_changes']} declared individual owner(s) have no "
                f"changes here in the last {ACTIVITY_WINDOW_DAYS} days.",
            )
        )

    covered = {p for paths in owned.values() for p in paths}
    out["files_with_declared_owner"] = len(covered)
    if teams:
        out["declared_teams"] = sorted(teams, key=lambda t: (-t["files"], t["team"]))
    if individuals["count"]:
        out["declared_individuals"] = individuals
    if unresolved:
        # Bare handles that may be a person or a group: counted, never named.
        out["declared_unresolved"] = unresolved
    if activity.available:
        out["recent"] = {
            "commits": len({s for p in files for s in activity.recent_commits.get(p, ())}),
            "contributors": len({k for p in files for k in activity.recent.get(p, ())}),
            "window_days": ACTIVITY_WINDOW_DAYS,
        }
        if activity.as_of is not None:
            out["recent"]["as_of"] = activity.as_of.date().isoformat()

    if critical:
        if not owned:
            findings.append(
                _finding(
                    "critical_no_active_owner",
                    f"Criticality {comp.criticality}, and no team or person is declared as owner.",
                )
            )
        elif states and all(s == "no_recent_changes" for s in states):
            findings.append(
                _finding(
                    "critical_no_active_owner",
                    f"Criticality {comp.criticality}, and no declared owner changed it in the "
                    f"last {ACTIVITY_WINDOW_DAYS} days.",
                )
            )
        if bus_factor and bus_factor.get("basis") == "measured" and bus_factor.get("value") == 1:
            findings.append(
                _finding(
                    "critical_bus_factor_one",
                    f"Criticality {comp.criticality}, and one person is the primary owner of "
                    "over half of its owned files.",
                )
            )
    out["findings"] = findings
    out["basis"] = "measured" if activity.available else "unavailable"
    out["method"] = (
        "declared owners from the catalog spec.owner and CODEOWNERS; activity from indexed "
        f"commits in the {ACTIVITY_WINDOW_DAYS} days before the newest one, matched through "
        "declared team members, git emails and forge noreply logins"
    )
    return out


def _declared_files(
    comp: Component, files: list[str], declared: DeclaredOwnership
) -> dict[OwnerRef, list[str]]:
    """Each declared owner of the component and the files it is declared for."""
    owned: dict[OwnerRef, dict[str, None]] = {}
    if comp.owner:
        owned[declared.from_backstage(comp.owner)] = dict.fromkeys(files)
    if declared.rules:
        for path in files:
            for owner in declared.codeowners_of(path):
                owned.setdefault(declared.from_codeowners(owner), {})[path] = None
    return {ref: list(paths) for ref, paths in owned.items()}


def _activity_state(ref: OwnerRef, paths: list[str], activity: Activity) -> str:
    keys = activity.keys_of(ref)
    if keys is None:
        return "unknown"
    if any(keys & activity.recent.get(p, set()) for p in paths):
        return "active"
    return "no_recent_changes"


def _finding(kind: str, message: str) -> dict[str, str]:
    return {"kind": kind, "message": message}
