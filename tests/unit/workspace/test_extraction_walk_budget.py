"""Traversal budget: one tree walk and one boundary detection per repo.

Five extractors each walked the repo and re-read every file whose extension it
claimed, and boundary detection then ran a sixth walk — per repo, per update.
Wall time is machine-dependent and makes a poor gate, but walk and read counts
are exact, so they are what these tests assert.

Measured on the three-repo workspace this change was developed against
(``repowise`` + ``backend`` + ``frontend``), for ``run_contract_extraction``:

===========================  ========  =======
metric                       before    after
===========================  ========  =======
FileTraverser walks / repo   5         1
file reads (all repos)       13,889    3,216
wall time                    45.23s    15.46s
contracts / links            714 / 101 714 / 101
===========================  ========  =======

Boundary detection keeps its own walk through ``fs_walk.walk_repo``, so the
per-repo total is 2 rather than 1; what changed there is that it runs once per
update instead of once for contract extraction and again for the system graph.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from repowise.core.ingestion.traverser import FileTraverser
from repowise.core.workspace import contracts as contracts_mod
from repowise.core.workspace import system_graph as system_graph_mod
from repowise.core.workspace.config import ContractConfig, RepoEntry, WorkspaceConfig
from repowise.core.workspace.contracts import run_contract_extraction

REPO_SOURCE = {
    "svc_a/api.py": (
        'router = APIRouter(prefix="/api")\n\n\n@router.get("/users")\ndef users(): ...\n'
    ),
    "svc_a/db.py": 'Q = "SELECT id FROM users WHERE id = :id"\n',
    "svc_a/pyproject.toml": '[project]\nname = "svc-a"\n',
    "svc_b/client.ts": "await fetch('/api/users');\n",
    "svc_b/package.json": '{"name": "svc-b"}\n',
    "svc_b/queue.js": "channel.sendToQueue('jobs', payload);\n",
}

# Source files in the extractor's combined extension set.  The manifests
# (pyproject.toml, package.json) are not in this set: .toml/.json are not
# among the extensions any extractor claims, so iter_source_files never reads
# them via read_bytes.  pyproject.toml is still read once via read_text by
# _collect_console_scripts during FileTraverser.traverse.
_SOURCE_FILES = frozenset(
    rel for rel in REPO_SOURCE if not rel.endswith((".toml", ".json"))
)


def _make_repo(root: Path) -> None:
    for rel, content in REPO_SOURCE.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    (root / ".repowise").mkdir(exist_ok=True)


@pytest.fixture
def workspace(tmp_path: Path) -> WorkspaceConfig:
    for alias in ("alpha", "beta"):
        _make_repo(tmp_path / alias)
    return WorkspaceConfig(
        repos=[RepoEntry(path="alpha", alias="alpha"), RepoEntry(path="beta", alias="beta")],
        contracts=ContractConfig(),
    )


@pytest.fixture
def counters(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> dict:
    """Count tree walks, file reads (scoped to the fixture repos) and boundary detections.

    ``Path.read_text`` and ``Path.read_bytes`` are patched at the class level because
    that is the only interception point that catches every call site.  Without a path
    filter the counter is process-global: reads by unrelated threads (leaked aiosqlite
    workers, pytest internals) increment the same counter the assertion checks.

    The filter restricts counting to paths under *tmp_path* — the two fixture repos
    live there and nowhere else, so nothing outside the repos under test can pollute
    the tally.  ``FileTraverser.traverse`` is similarly scoped to this process's
    constructors: only repowise code builds a ``FileTraverser``, and each one carries
    ``repo_root``, which the closure checks against *tmp_path*.
    """
    counts: dict = {"walks": 0, "reads_per_path": {}, "boundaries": 0}

    def _under_fixture(p: Path) -> bool:
        """True when *p* resolves to a path under the fixture workspace."""
        try:
            p.resolve().relative_to(tmp_path)
            return True
        except ValueError:
            return False

    # --- FileTraverser.traverse -------------------------------------------
    original_traverse = FileTraverser.traverse

    def counted_traverse(self, *args, **kwargs):
        if _under_fixture(self.repo_root):
            counts["walks"] += 1
        return original_traverse(self, *args, **kwargs)

    # --- Path.read_text / read_bytes --------------------------------------
    # Both, because the shared walk reads bytes (so it can hash them) while
    # other call sites still read text.  Counting only one would let a second
    # read slip in through the other.
    original_read_text = Path.read_text
    original_read_bytes = Path.read_bytes

    def counted_read_text(self, *args, **kwargs):
        if _under_fixture(self):
            key = str(self.resolve())
            counts["reads_per_path"][key] = counts["reads_per_path"].get(key, 0) + 1
        return original_read_text(self, *args, **kwargs)

    def counted_read_bytes(self, *args, **kwargs):
        if _under_fixture(self):
            key = str(self.resolve())
            counts["reads_per_path"][key] = counts["reads_per_path"].get(key, 0) + 1
        return original_read_bytes(self, *args, **kwargs)

    monkeypatch.setattr(FileTraverser, "traverse", counted_traverse)
    monkeypatch.setattr(Path, "read_text", counted_read_text)
    monkeypatch.setattr(Path, "read_bytes", counted_read_bytes)

    from repowise.core.workspace import extractors as extractors_pkg
    from repowise.core.workspace.extractors import service_boundary

    original_detect = service_boundary.detect_service_boundaries

    def counted_detect(repo_path, *args, **kwargs):
        counts["boundaries"] += 1
        return original_detect(repo_path, *args, **kwargs)

    for module in (service_boundary, extractors_pkg, system_graph_mod):
        monkeypatch.setattr(module, "detect_service_boundaries", counted_detect)
    return counts


async def test_one_tree_walk_per_repo(
    workspace: WorkspaceConfig,
    tmp_path: Path,
    counters: dict,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(contracts_mod, "save_contract_store", lambda store, root: root)
    await run_contract_extraction(workspace, tmp_path, [])
    assert counters["walks"] == 2  # one per repo, not one per extractor per repo


async def test_each_file_is_read_once_per_repo(
    workspace: WorkspaceConfig,
    tmp_path: Path,
    counters: dict,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(contracts_mod, "save_contract_store", lambda store, root: root)
    await run_contract_extraction(workspace, tmp_path, [])

    reads = counters["reads_per_path"]

    # Every file under the fixture repos is read at most once per repo:
    #
    # - Source files (.py, .ts, .js) are read once via read_bytes inside
    #   iter_source_files, which is the shared walk.
    # - pyproject.toml is read once via read_text by _collect_console_scripts
    #   during FileTraverser.traverse (entry-point detection).  It is NOT read
    #   by iter_source_files because .toml is not in any extractor's extension
    #   set.
    # - package.json is traversed for language detection but never opened:
    #   .json is not in any extractor's extension set, and json/toml are in
    #   _SKIP_GENERATED_CHECK so the 512-byte generated-file read is skipped.
    #
    # A second read of any file means an extractor re-read outside the shared
    # walk — the regression this test exists to catch.
    MAX_READS_PER_FILE = 1
    over_budget = {path: n for path, n in reads.items() if n > MAX_READS_PER_FILE}
    assert not over_budget, (
        f"Files read more than {MAX_READS_PER_FILE} time(s) — "
        "an extractor is re-reading outside the shared walk:\n"
        + "\n".join(
            f"  {n}x  {path}" for path, n in sorted(over_budget.items())
        )
    )


async def test_boundaries_detected_once_per_repo_when_supplied(
    workspace: WorkspaceConfig,
    tmp_path: Path,
    counters: dict,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(contracts_mod, "save_contract_store", lambda store, root: root)
    boundaries = system_graph_mod._detect_boundaries_by_repo(workspace, tmp_path)
    detections_for_the_shared_map = counters["boundaries"]

    await run_contract_extraction(workspace, tmp_path, [], boundaries)

    # Extraction reuses the map instead of re-detecting per repo.
    assert counters["boundaries"] == detections_for_the_shared_map


async def test_boundaries_are_detected_when_not_supplied(
    workspace: WorkspaceConfig,
    tmp_path: Path,
    counters: dict,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(contracts_mod, "save_contract_store", lambda store, root: root)
    await run_contract_extraction(workspace, tmp_path, [])
    assert counters["boundaries"] == 2  # direct callers still get boundaries


async def test_hooks_detect_boundaries_once_per_repo(
    workspace: WorkspaceConfig,
    tmp_path: Path,
    counters: dict,
) -> None:
    """The 2-per-repo -> 1-per-repo budget, through the caller that wires it.

    ``run_cross_repo_hooks`` is where contract extraction and the system-graph
    build meet; each used to detect boundaries for every repo independently.
    """
    from repowise.core.workspace.update import run_cross_repo_hooks

    await run_cross_repo_hooks(workspace, tmp_path, [])

    assert counters["boundaries"] == 2  # two repos, once each — not 4


async def test_the_shared_walk_finds_the_same_contracts(
    workspace: WorkspaceConfig,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(contracts_mod, "save_contract_store", lambda store, root: root)
    store = await run_contract_extraction(workspace, tmp_path, [])
    ids = {c.contract_id for c in store.contracts}
    assert "http::GET::/api/users" in ids  # provider, prefix stitched
    assert "data::users" in ids  # SQL consumer
    assert "topic::jobs" in ids  # queue provider


async def test_every_contract_records_its_extraction_layer(
    workspace: WorkspaceConfig,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from repowise.core.workspace.extractors.from_index import (
        EXTRACTION_LAYER_KEY,
        LAYER_REGEX,
    )

    monkeypatch.setattr(contracts_mod, "save_contract_store", lambda store, root: root)
    store = await run_contract_extraction(workspace, tmp_path, [])
    assert store.contracts
    # No wiki.db in these fixture repos, so everything is regex-sourced.
    assert {c.meta.get(EXTRACTION_LAYER_KEY) for c in store.contracts} == {LAYER_REGEX}


async def test_grpc_dialect_provenance_survives_the_layer_stamp(
    workspace: WorkspaceConfig,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A gRPC contract keeps its own ``meta["source"]`` and gains a layer."""
    from repowise.core.workspace.extractors.from_index import (
        EXTRACTION_LAYER_KEY,
        LAYER_REGEX,
    )

    (tmp_path / "alpha" / "svc_a" / "auth.proto").write_text(
        'syntax = "proto3";\n\nservice AuthService {\n'
        "  rpc Login (LoginRequest) returns (LoginReply);\n}\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(contracts_mod, "save_contract_store", lambda store, root: root)
    store = await run_contract_extraction(workspace, tmp_path, [])

    grpc = [c for c in store.contracts if c.contract_type == "grpc"]
    assert grpc, "fixture should produce a gRPC contract"
    for contract in grpc:
        assert contract.meta["source"] == "proto"  # dialect provenance intact
        assert contract.meta[EXTRACTION_LAYER_KEY] == LAYER_REGEX
