"""Contracts inside one repository: ``.repowise/contracts.json``, opt-in.

The workspace run (:func:`.contracts.run_contract_extraction`) matches callers
in one repo to routes in another. A monorepo holds both sides in one checkout,
split into services by :func:`.extractors.detect_service_boundaries`, and the
matcher already links across services within a repo. This runs the same
extractors, the same matching and the same store format over that one repo,
when ``contracts.in_repo`` is on, at the end of ``init`` and ``update``.

Data and package-surface contracts stay off: a table two packages of one
program share is not an API contract, and a package surface needs another repo
to publish it.

Incremental by HEAD and the dirty tree. The store's ``repo_provenance`` row
names the HEAD it was read at, the extraction settings, and a fingerprint of
the uncommitted files that can hold a contract. A run re-extracts only when a
commit since that HEAD, or an uncommitted edit, touches such a file; otherwise
the stored rows stand (and a moved HEAD is re-stamped). Anything it cannot
answer (no git, an unreadable diff) resolves toward re-extraction.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING

from repowise.core.workspace.config import ContractConfig
from repowise.core.workspace.contracts import (
    CONTRACTS_FILENAME,
    CONTRACTS_VERSION,
    ContractStore,
    normalize_contract_id,
    write_contract_store,
)

if TYPE_CHECKING:
    from repowise.core.workspace.breaking_change import BreakingChange

_log = logging.getLogger("repowise.workspace.repo_contracts")

#: ``repo_provenance`` key holding the uncommitted-files fingerprint.
DIRTY_FP_KEY = "dirty_fp"
#: ``repo_provenance`` key: "1" when the rows were read with wiki.db open.
INDEXED_KEY = "indexed"


def repo_contracts_path(repo_path: Path) -> Path:
    return repo_path / ".repowise" / CONTRACTS_FILENAME


def repo_contract_config() -> ContractConfig:
    """HTTP, gRPC, socket and topic only (see the module docstring)."""
    return ContractConfig(detect_data=False, detect_code_api=False)


@dataclass(frozen=True)
class RepoContractsResult:
    """What one run did: ``extracted`` false means the stored rows were kept."""

    extracted: bool
    contracts: int
    #: Call-to-route links inside the repo (``contract_links``).
    links: int
    path: Path
    #: The rows a re-extraction replaced, when they were read the same way (same
    #: settings, same index state); None on a first run, a reuse, or rows that
    #: cannot be compared, so a diff only ever reflects a change in the code.
    previous: ContractStore | None = None
    #: The rows a re-extraction wrote; None when the stored rows were kept.
    current: ContractStore | None = None
    #: Host-only edges to another repo whose route is unread (``service_links``).
    service_links: int = 0


async def run_repo_contract_extraction(repo_path: Path) -> RepoContractsResult:
    """Extract and match *repo_path*'s contracts into ``.repowise/contracts.json``.

    Raises on failure; the file is written atomically, so a failed run leaves
    the previous one in place. Does not read ``contracts.in_repo``: the
    caller gates on it before importing this module.
    """
    from .contracts import _extraction_config_fingerprint
    from .extractors.masstransit import MessageTypeIndex
    from .update import get_head_commit

    repo_path = repo_path.resolve()
    alias = repo_path.name
    out_path = repo_contracts_path(repo_path)
    contract_config = repo_contract_config()
    wanted = _relevance(contract_config)
    excludes = _repo_exclude_patterns(repo_path)
    # The repo's own exclude_patterns decide the walk too, so they key reuse.
    config_fp = hashlib.sha256(
        json.dumps(
            [_extraction_config_fingerprint(contract_config, set(), MessageTypeIndex()), excludes]
        ).encode()
    ).hexdigest()[:16]

    head, dirty_fp = await asyncio.gather(
        asyncio.to_thread(get_head_commit, repo_path),
        asyncio.to_thread(_dirty_fingerprint, repo_path, wanted),
    )
    prior = _read_quietly(out_path)
    stamped = (prior.repo_provenance.get(alias) if prior is not None else None) or {}
    if prior is not None and _stamp_holds(stamped, prior, head, dirty_fp, config_fp, repo_path):
        assert head is not None
        if stamped["head"] == head:
            return RepoContractsResult(
                False,
                len(prior.contracts),
                len(prior.contract_links),
                out_path,
                service_links=len(prior.service_links),
            )
        if await asyncio.to_thread(
            _commits_leave_contracts_alone, repo_path, stamped["head"], head, wanted
        ):
            # Same rows, read at an older commit no contract file has moved
            # since: re-stamp so the next diff starts here.
            prior.repo_provenance[alias] = {**stamped, "head": head}
            write_contract_store(prior, out_path)
            return RepoContractsResult(
                False,
                len(prior.contracts),
                len(prior.contract_links),
                out_path,
                service_links=len(prior.service_links),
            )

    store, indexed = await _extract(repo_path, alias, contract_config, head, excludes)
    store.repo_provenance = {
        alias: {
            "head": head or "",
            "extracted_at": store.generated_at,
            "config_fp": config_fp,
            DIRTY_FP_KEY: dirty_fp or "",
            INDEXED_KEY: "1" if indexed else "",
        }
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    write_contract_store(store, out_path)
    comparable = (
        prior is not None
        and prior.version == CONTRACTS_VERSION
        and stamped.get("config_fp") == config_fp
        and (stamped.get(INDEXED_KEY) == "1") == indexed
    )
    return RepoContractsResult(
        True,
        len(store.contracts),
        len(store.contract_links),
        out_path,
        previous=prior if comparable else None,
        current=store,
        service_links=len(store.service_links),
    )


@dataclass(frozen=True)
class RepoContractChange:
    """How a re-extraction moved the repo's contracts.

    ``changed`` counts contract ends added, removed or changed in shape, on
    either side. ``breaking`` holds one breaking change per contract with a
    consumer in another service, the ones a caller in this repo will feel.
    """

    changed: int
    breaking: list[BreakingChange]


def diff_repo_contracts(previous: ContractStore, current: ContractStore) -> RepoContractChange:
    """The workspace breaking-change diff, filtered by (repo, service).

    A workspace gates on a consumer in another repo; inside one repo the
    boundary that matters is the service, so a break counts only when a caller
    outside the provider's own service is linked to it.
    """
    from .breaking_change import (
        SEVERITY_BREAKING,
        consumers_outside_service,
        detect_breaking_changes,
    )

    def ends(store: ContractStore) -> set[tuple[str, str | None, str]]:
        return {(c.role, c.service, normalize_contract_id(c.contract_id)) for c in store.contracts}

    # The engine indexes providers by contract id alone, and two services of
    # one repo can serve the same id: diffed together, one service's removal
    # hides behind the other's surviving copy. Diff each service on its own.
    prev_parts, curr_parts = _by_provider_service(previous), _by_provider_service(current)
    changes: list[BreakingChange] = []
    for owner in sorted(set(prev_parts) | set(curr_parts), key=str):
        empty = ContractStore()
        changes += detect_breaking_changes(
            prev_parts.get(owner, empty), curr_parts.get(owner, empty)
        ).changes
    changed = (ends(previous) ^ ends(current)) | {
        ("provider", c.provider_service, normalize_contract_id(c.contract_id)) for c in changes
    }
    # One entry per broken contract, counted in the same unit as ``changed``:
    # a route that lost three fields is one break, named once.
    breaking: dict[tuple[str, str | None, str], BreakingChange] = {}
    for c in changes:
        if c.severity == SEVERITY_BREAKING and consumers_outside_service(c):
            key = (c.provider_repo, c.provider_service, normalize_contract_id(c.contract_id))
            breaking.setdefault(key, c)
    return RepoContractChange(changed=len(changed), breaking=list(breaking.values()))


def _by_provider_service(store: ContractStore) -> dict[tuple[str, str | None], ContractStore]:
    """*store*'s providers and their links, one store per (repo, service)."""
    parts: dict[tuple[str, str | None], ContractStore] = {}
    for c in store.contracts:
        if c.role == "provider":
            parts.setdefault((c.repo, c.service), ContractStore()).contracts.append(c)
    for lk in store.contract_links:
        part = parts.get((lk.provider_repo, lk.provider_service))
        if part is not None:
            part.contract_links.append(lk)
    return parts


def _stamp_holds(
    stamped: dict[str, str],
    prior: ContractStore,
    head: str | None,
    dirty_fp: str | None,
    config_fp: str,
    repo_path: Path,
) -> bool:
    """Whether the stored rows were read under today's settings and dirty tree.

    HEAD is checked by the caller: a moved HEAD can still keep the rows when no
    commit since touched a contract file. Anything unknown answers no.
    """
    return (
        prior.version == CONTRACTS_VERSION
        and head is not None
        and dirty_fp is not None
        and bool(stamped.get("head"))
        and stamped.get("config_fp") == config_fp
        and stamped.get(DIRTY_FP_KEY) == dirty_fp
        # Rows read before ingestion wrote wiki.db lack handler bindings and
        # code origins; once the index exists they are read again.
        and (stamped.get(INDEXED_KEY) == "1" or not _has_index(repo_path))
    )


async def _extract(
    repo_path: Path,
    alias: str,
    contract_config: ContractConfig,
    head: str | None,
    excludes: list[str],
) -> tuple[ContractStore, bool]:
    """One full read of *repo_path*, matched within itself, and whether wiki.db
    was read; nothing written."""
    from .contracts import _extract_one_repo, _RepoExtraction, link_contracts
    from .extractors import build_message_type_index
    from .extractors.base import make_exclude_predicate
    from .extractors.masstransit import MessageTypeIndex
    from .repo_index import WorkspaceIndex, open_repo_index
    from .service_names import build_service_name_map, service_names_fingerprint

    exclude = _with_repo_excludes(
        make_exclude_predicate(tuple(contract_config.exclude_globs)), excludes
    )
    repos = {alias: repo_path}
    message_types = (
        await asyncio.to_thread(
            build_message_type_index, repos, exclude, contract_config.contract_project_pattern
        )
        if contract_config.detect_topics
        else MessageTypeIndex()
    )
    # The symbols ingestion stored bind routes to handlers and give each end
    # its code origin; without an index the regex dialects stand alone.
    indexed = _has_index(repo_path)
    try:
        index = await open_repo_index(alias, repo_path)
    except LookupError:
        index = None  # wiki.db with no repository row: read without it
    workspace_index = WorkspaceIndex({alias: index} if index is not None else {})
    try:
        from .code_api import CodeSurface

        extraction = _RepoExtraction(
            contract_config=contract_config,
            exclude=exclude,
            code_surface=CodeSurface(),
            message_types=message_types,
            boundaries_by_repo=None,
            workspace_index=workspace_index,
        )
        contracts, stats = await _extract_one_repo(extraction, alias, repo_path)
    finally:
        await workspace_index.close()

    names = await asyncio.to_thread(build_service_name_map, repos, None)
    matched = link_contracts(contracts, contract_config.service_bases, names)
    store = ContractStore(
        version=CONTRACTS_VERSION,
        generated_at=datetime.now(UTC).isoformat(),
        contracts=contracts,
        contract_links=matched.links,
        extraction_stats={alias: stats} if stats else {},
        service_links=matched.service_links,
        service_names=names.to_dict(service_names_fingerprint({alias: head}, None)),
    )
    # Whether wiki.db was there to read, usable or not: an empty one is not
    # re-read on every run (see _stamp_holds).
    return store, indexed


def _repo_exclude_patterns(repo_path: Path) -> list[str]:
    """The repo's ``exclude_patterns`` from ``.repowise/config.yaml``, as the index reads them."""
    from repowise.core.repo_config import load_repo_config

    try:
        patterns = load_repo_config(repo_path).get("exclude_patterns") or []
    except Exception:
        return []  # a broken config is reported by the command that owns it
    return [str(p) for p in patterns] if isinstance(patterns, list) else []


def _with_repo_excludes(base: Callable[[str], bool], patterns: list[str]) -> Callable[[str], bool]:
    """*base*, plus the gitignore-style patterns the index's own walk skips."""
    if not patterns:
        return base
    from repowise.core.ingestion.traverser import _compile_gitignore

    spec = _compile_gitignore(patterns)
    return lambda rel: base(rel) or spec.match_file(rel)


def _has_index(repo_path: Path) -> bool:
    from .registry import repo_db_path

    return repo_db_path(repo_path).is_file()


def _read_quietly(path: Path) -> ContractStore | None:
    """The stored file, or None; a damaged one is simply re-extracted."""
    try:
        return ContractStore.from_dict(json.loads(path.read_text(encoding="utf-8")))
    except FileNotFoundError:
        return None
    except Exception as exc:
        _log.debug("unreadable %s, re-extracting: %r", path, exc)
        return None


@dataclass(frozen=True)
class _Relevance:
    """Which paths can change what extraction reads."""

    extensions: frozenset[str]

    def __call__(self, rel_path: str) -> bool:
        """True for a file an extractor reads, a service marker or a service-name source.

        Liberal on purpose: a false yes costs one re-extraction, a false no
        leaves the store stale.
        """
        from .manifests import SERVICE_MARKERS
        from .service_names import SOURCES

        path = PurePosixPath(rel_path.replace("\\", "/"))
        if path.parts[:1] in ((".repowise",), (".repowise-workspace",)):
            return False  # repowise's own output, this file among it
        name = path.name
        if path.suffix.lower() in self.extensions or name in SERVICE_MARKERS:
            return True
        if name in (".gitignore", ".repowiseignore"):
            return True  # moves what the walk sees
        parents = tuple(p.lower() for p in path.parts[:-1])
        return any(source.gates(name, parents) for source in SOURCES)


def _relevance(contract_config: ContractConfig) -> _Relevance:
    from .contracts import _enabled_extractors
    from .extractors.service_boundary import _SOURCE_EXTENSIONS

    # A first source file under a marker makes a new service boundary.
    extensions: frozenset[str] = frozenset(_SOURCE_EXTENSIONS)
    for extractor in _enabled_extractors(contract_config):
        extensions |= extractor.source_extensions()
    return _Relevance(frozenset(e.lower() for e in extensions))


def _git_lines(repo_path: Path, args: list[str]) -> list[str] | None:
    """NUL-separated paths from one git call, relative to *repo_path*; None on failure."""
    from repowise.core.analysis.git_cli import run_git

    try:
        result = run_git(args, repo_path, timeout=60)
    except Exception:
        return None
    if result.returncode != 0:
        return None
    return [p for p in result.stdout.split("\0") if p]


def _dirty_fingerprint(repo_path: Path, relevant: _Relevance) -> str | None:
    """Hash of every uncommitted file that can hold a contract, path and bytes.

    Staged, unstaged, untracked and ignored alike: the service-name walk reads
    an ignored ``.env`` or compose override. An ignored directory is listed
    whole by ``--directory`` and skipped; the walks prune the usual ones
    (``node_modules``, ``.venv``, build output), and a source tree kept only in
    an ignored folder is the ceiling here. None when git cannot say, which
    forces a re-extraction.
    """
    changed = _git_lines(
        repo_path, ["diff", "--name-only", "--no-renames", "--relative", "-z", "HEAD"]
    )
    untracked = _git_lines(repo_path, ["ls-files", "--others", "--exclude-standard", "-z"])
    ignored = _git_lines(
        repo_path,
        ["ls-files", "--others", "--ignored", "--exclude-standard", "--directory", "-z"],
    )
    if changed is None or untracked is None or ignored is None:
        return None
    h = hashlib.sha256()
    for rel in sorted({*changed, *untracked, *ignored}):
        if rel.endswith("/") or not relevant(rel):
            continue
        try:
            digest = hashlib.sha256((repo_path / rel).read_bytes()).hexdigest()
        except OSError:
            digest = "missing"  # deleted in the tree
        h.update(json.dumps([rel, digest]).encode())
    return h.hexdigest()[:16]


def _commits_leave_contracts_alone(
    repo_path: Path, base: str, head: str, relevant: _Relevance
) -> bool:
    """True when no file between *base* and *head* can hold a contract."""
    paths = _git_lines(
        repo_path, ["diff", "--name-only", "--no-renames", "--relative", "-z", base, head]
    )
    return paths is not None and not any(relevant(p) for p in paths)


__all__ = [
    "DIRTY_FP_KEY",
    "RepoContractChange",
    "RepoContractsResult",
    "diff_repo_contracts",
    "repo_contract_config",
    "repo_contracts_path",
    "run_repo_contract_extraction",
]
