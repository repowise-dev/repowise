"""Component facts for one index run, read from the stores that run left behind.

The figures reuse the component rollups (:func:`component_rollups`) and add
what a diff needs on top: the full hotspot set, observed ownership as opaque
digests, governing decisions with their staleness, and the provided contract
set. Nothing here names a
person: owners are counted, and the main-owner set is kept only as HMAC digests
under a per-repository random key (``.repowise/events.key``), which is all a
set comparison between two runs needs. A digest cannot be checked against a
guessed email without the key. A declared owner is named only when it is a
group ref.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import secrets
from collections import Counter
from datetime import timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import func, select

logger = logging.getLogger(__name__)

#: Bumped when a fact's definition changes; part of the stamp.
SNAPSHOT_FACTS_VERSION = 1
#: Window for "recent" activity, back from the newest indexed commit.
RECENT_DAYS = 90
#: A governing decision at or above this staleness counts as stale
#: (the threshold ``get_stale_decisions`` uses).
STALE_THRESHOLD = 0.5
#: Ceiling on ``(file, commit)`` rows read for the recent window, newest first.
#: Past it the window is partial and says so (``recent_capped``); the rules
#: that read recent activity skip it. Upgrade path: aggregate per component in SQL.
MAX_RECENT_ROWS = 200_000
#: Per-repository key the owner digests are made with.
OWNER_KEY_FILE = "events.key"


def current_stamp() -> str:
    """The analyzer and parser versions facts are computed under, as one string."""
    from repowise.core.analysis.health.engine import HEALTH_ANALYZER_VERSION
    from repowise.core.ingestion.git_indexer._constants import GIT_HISTORY_VERSION
    from repowise.core.upgrade.version import PARSER_SCHEMA_VERSION
    from repowise.core.workspace.contracts import CONTRACTS_VERSION

    return (
        f"h{HEALTH_ANALYZER_VERSION}.p{PARSER_SCHEMA_VERSION}.g{GIT_HISTORY_VERSION}"
        f".c{CONTRACTS_VERSION}.s{SNAPSHOT_FACTS_VERSION}"
    )


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]


def owner_key(repo_root: Path) -> bytes | None:
    """The repository's digest key, made on first use; ``None`` when it cannot
    be read or written. A new key gets a new id, so the next diff of owner
    sets is skipped rather than reading every owner as replaced."""
    from repowise.core.repo_config import write_private_file

    path = Path(repo_root) / ".repowise" / OWNER_KEY_FILE
    try:
        if path.is_file():
            key = bytes.fromhex(path.read_text(encoding="ascii").strip())
            if len(key) >= 16:
                return key
        key = secrets.token_bytes(32)
        write_private_file(
            repo_root, OWNER_KEY_FILE, key.hex(), comment="repowise owner-digest key (local)"
        )
        return key
    except (OSError, ValueError) as exc:
        logger.warning("owner-digest key unavailable, owner sets not kept: %s", exc)
        return None


def _keyed(key: bytes, text: str) -> str:
    return hmac.new(key, text.encode("utf-8"), hashlib.sha256).hexdigest()[:16]


async def collect_facts(
    session: Any,
    repository_id: str,
    repo_root: Path,
    *,
    head_commit: str | None,
) -> dict[str, dict[str, Any]]:
    """Facts per component name for the repository's current index."""
    from repowise.core.components.store import component_rollups

    found, files = await _components(session, repository_id, repo_root)
    if not files:
        return {}
    rollups = await component_rollups(session, repository_id, files, found.test_files)
    git_rows = await _git_rows(session, repository_id)
    recent = await _recent_commit_files(session, repository_id)
    capped = len(recent) >= MAX_RECENT_ROWS
    key = owner_key(repo_root)
    key_id = _digest(key.hex()) if key else None
    governing, stale = await governing_decisions(session, repository_id)
    contracts = provided_contracts(repo_root, head_commit)

    from repowise.core.analysis.owners import people_resolver
    from repowise.core.components.catalog import owner_group_name

    resolve = people_resolver(git_rows.values(), recent)
    recent_by_file: dict[str, list[dict[str, Any]]] = {}
    for row in recent:
        recent_by_file.setdefault(row["file_path"], []).append(row)

    out: dict[str, dict[str, Any]] = {}
    for comp in found.resolved.components:
        paths = files.get(comp.name)
        if not paths:
            continue
        rolled = rollups.get(comp.name, {})
        commits = {r["sha"]: r for p in paths for r in recent_by_file.get(p, ())}
        facts: dict[str, Any] = {
            "definition": _definition(comp),
            "criticality": comp.criticality,
            "tags": sorted(comp.tags),
            # Named only as a group; any other owner ref is compared by digest.
            "declared_owner": owner_group_name(comp.owner),
            "declared_owner_key": _keyed(key, comp.owner) if key and comp.owner else None,
            "key_id": key_id,
            "file_count": len(paths),
            "health": rolled.get("health"),
            "bus_factor": rolled.get("bus_factor"),
            "test_reach": rolled.get("test_reach"),
            "dead_code": rolled.get("dead_code"),
            "hotspots": sorted(p for p in paths if git_rows.get(p, {}).get("is_hotspot")),
            "ownership": _ownership(paths, git_rows, commits.values(), resolve, key, capped),
            "decisions": _component_decisions(paths, governing, stale),
            "contracts": _component_contracts(paths, contracts),
        }
        out[comp.name] = facts
    return out


async def contract_facts(
    session: Any, repository_id: str, repo_root: Path, *, head_commit: str | None
) -> dict[str, tuple[str, dict[str, Any]]] | None:
    """``{component: (definition, contracts fact)}`` measured now; ``None``
    while the contracts at *head_commit* are unavailable."""
    contracts = provided_contracts(repo_root, head_commit)
    if contracts is None:
        return None
    found, files = await _components(session, repository_id, repo_root)
    return {
        comp.name: (_definition(comp), _component_contracts(files[comp.name], contracts))
        for comp in found.resolved.components
        if files.get(comp.name)
    }


async def _components(session: Any, repository_id: str, repo_root: Path) -> tuple[Any, dict]:
    from repowise.core.components.store import load_repo_components, workspace_services

    found = await load_repo_components(
        session, repository_id, repo_root, services=workspace_services(repo_root)
    )
    return found, {name: paths for name, paths in found.resolved.files.items() if paths}


def _definition(comp: Any) -> str:
    return _digest(json.dumps([sorted(comp.paths), comp.root, comp.source]))


async def _git_rows(session: Any, repository_id: str) -> dict[str, dict[str, Any]]:
    from repowise.core.persistence.crud import code_file_rows
    from repowise.core.persistence.models import GitMetadata

    rows = await session.execute(
        select(
            GitMetadata.file_path,
            GitMetadata.is_hotspot,
            GitMetadata.primary_owner_name,
            GitMetadata.primary_owner_email,
            GitMetadata.top_authors_json,
        ).where(code_file_rows(repository_id))
    )
    return {r.file_path: dict(r._mapping) for r in rows}


async def _recent_commit_files(session: Any, repository_id: str) -> list[dict[str, Any]]:
    """``(file, commit)`` rows of the window before the newest indexed commit.

    Anchored on that commit, not the clock, so a snapshot of an old checkout
    reads the activity around it.
    """
    from repowise.core.persistence.models import GitCommit, GitCommitFile

    newest = await session.scalar(
        select(func.max(GitCommit.committed_at)).where(GitCommit.repository_id == repository_id)
    )
    if newest is None:
        return []
    rows = await session.execute(
        select(
            GitCommitFile.file_path,
            GitCommit.sha,
            GitCommit.author_name,
            GitCommit.author_email,
        )
        .join(
            GitCommit,
            (GitCommit.repository_id == GitCommitFile.repository_id)
            & (GitCommit.sha == GitCommitFile.sha),
        )
        .where(
            GitCommitFile.repository_id == repository_id,
            GitCommit.committed_at >= newest - timedelta(days=RECENT_DAYS),
        )
        .order_by(GitCommit.committed_at.desc())
        .limit(MAX_RECENT_ROWS)
    )
    return [dict(r._mapping) for r in rows]


async def governing_decisions(
    session: Any, repository_id: str
) -> tuple[dict[str, list[tuple[str, str]]], set[str]]:
    """Governing ``(id, title)`` pairs per file, and the ids at or past :data:`STALE_THRESHOLD`."""
    from repowise.core.persistence.crud.authority import governing_decisions_by_file
    from repowise.core.persistence.models import DecisionRecord

    governing = await governing_decisions_by_file(session, repository_id)
    ids = {d for pairs in governing.values() for d, _ in pairs}
    if not ids:
        return governing, set()
    stale = await session.scalars(
        select(DecisionRecord.id).where(
            DecisionRecord.repository_id == repository_id,
            DecisionRecord.id.in_(sorted(ids)),
            DecisionRecord.staleness_score >= STALE_THRESHOLD,
        )
    )
    return governing, set(stale)


def provided_contracts(repo_root: Path, head_commit: str | None) -> dict[str, dict[str, int]] | None:
    """``{file: {contract_id: consumer links}}`` for this repo's provided contracts.

    ``None`` outside a workspace, or when the workspace's contracts were not
    extracted at this repo's current commit: an older extraction would read as
    contracts appearing and disappearing.
    """
    from repowise.core.workspace.config import WorkspaceConfig, find_workspace_root
    from repowise.core.workspace.contracts import load_contract_store

    ws_root = find_workspace_root(repo_root)
    if ws_root is None or not head_commit:
        return None
    try:
        config = WorkspaceConfig.load(ws_root)
    except Exception:
        return None
    root = repo_root.resolve()
    alias = next((r.alias for r in config.repos if (ws_root / r.path).resolve() == root), None)
    store = load_contract_store(ws_root) if alias else None
    if store is None or store.repo_provenance.get(alias, {}).get("head") != head_commit:
        return None
    consumers = Counter(
        (lk.provider_file, lk.contract_id)
        for lk in store.contract_links
        if lk.provider_repo == alias and lk.consumer_repo != alias
    )
    out: dict[str, dict[str, int]] = {}
    for c in store.rows_for_repo(alias):
        if c.role == "provider":
            out.setdefault(c.file_path, {})[c.contract_id] = consumers[(c.file_path, c.contract_id)]
    return out


def _ownership(
    paths: list[str],
    git_rows: dict[str, dict[str, Any]],
    commits: Any,
    resolve: Any,
    key: bytes | None,
    capped: bool,
) -> dict[str, Any]:
    """Observed ownership as counts and opaque digests of the main owners.

    Main owners are the fewest primary owners who together own over half the
    owned files (the bus-factor set). Digests let two runs compare the set
    without storing who is in it.
    """
    owners: Counter[str] = Counter()
    for p in paths:
        row = git_rows.get(p)
        name = row and row.get("primary_owner_name")
        if not name:
            continue
        person = resolve(name, row.get("primary_owner_email"))
        if person and not resolve.is_bot(person):
            owners[person] += 1
    owned = sum(owners.values())
    main: list[str] = []
    held = 0
    for person, count in sorted(owners.items(), key=lambda kv: (-kv[1], kv[0])):
        if held * 2 > owned:
            break
        main.append(person)
        held += count
    recent_authors = {resolve(c["author_name"], c["author_email"]) for c in commits}
    return {
        "owned_files": owned,
        "owner_count": len(owners),
        # Without a key there is nothing safe to compare, so no set is kept.
        "main_owners": sorted(_keyed(key, k) for k in main) if key else None,
        "active_main_owners": sum(1 for k in main if k in recent_authors),
        "recent_commits": len({c["sha"] for c in commits}),
        "recent_capped": capped,
        "window_days": RECENT_DAYS,
    }


def _component_decisions(
    paths: list[str], governing: dict[str, list[tuple[str, str]]], stale: set[str]
) -> dict[str, Any]:
    ids = sorted({d for p in paths for d, _ in governing.get(p, ())})
    return {"governing": len(ids), "stale": [d for d in ids if d in stale]}


def _component_contracts(
    paths: list[str], contracts: dict[str, dict[str, int]] | None
) -> dict[str, Any]:
    if contracts is None:
        return {"basis": "unavailable"}
    provided: dict[str, int] = {}
    for p in paths:
        for cid, n in contracts.get(p, {}).items():
            provided[cid] = provided.get(cid, 0) + n
    return {"basis": "measured", "provided": dict(sorted(provided.items()))}
