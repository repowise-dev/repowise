"""Incremental contract extraction: what gets reused, and what must not.

A one-line commit in one repo used to re-extract every repo in the workspace,
because ``changed_repos`` was accepted and never read. Scoping that is easy; the
part that needs tests is what happens to the *skipped* repos' rows.

The invariant these tests defend is that a repo's contracts, its
``extraction_stats`` row and its ``repo_provenance`` row move together or not at
all. Splitting them is what would let the coverage ratio take its numerator from
one commit and its denominator from another — a wrong number that still looks
like a number.

The other half is that reuse is validated rather than trusted. ``changed_repos``
is a hint from upstream; what is checked against live state is the repo's HEAD,
its working tree, and the contract config the persisted rows were produced
under. Every way of disagreeing resolves toward re-extraction.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from repowise.core.workspace import contracts as contracts_mod
from repowise.core.workspace.config import ContractConfig, RepoEntry, WorkspaceConfig
from repowise.core.workspace.contracts import ContractStore, run_contract_extraction

REPO_SOURCE = {
    "svc/api.py": ('router = APIRouter(prefix="/api")\n\n\n@router.get("/users")\ndef users(): ...\n'),
    "svc/db.py": 'Q = "SELECT id FROM users WHERE id = :id"\n',
    "svc/pyproject.toml": '[project]\nname = "svc"\n',
    # An HTTP consumer, so the annotation the carry-forward path has to
    # recompute actually exists in the fixture.
    "svc/client.ts": "await fetch('/api/users');\n",
}


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args], cwd=str(repo), capture_output=True, text=True, check=True
    )
    return result.stdout.strip()


def _make_git_repo(root: Path, alias: str) -> Path:
    """A real git checkout — these tests turn on HEAD actually moving."""
    root.mkdir(parents=True, exist_ok=True)
    for rel, content in REPO_SOURCE.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content.replace("/api", f"/{alias}"), encoding="utf-8")
    (root / ".repowise").mkdir(exist_ok=True)
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "test@example.com")
    _git(root, "config", "user.name", "Test")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "initial")
    return root


@pytest.fixture
def workspace(tmp_path: Path) -> WorkspaceConfig:
    for alias in ("alpha", "beta"):
        _make_git_repo(tmp_path / alias, alias)
    return WorkspaceConfig(
        repos=[RepoEntry(path="alpha", alias="alpha"), RepoEntry(path="beta", alias="beta")],
        contracts=ContractConfig(),
    )


@pytest.fixture
def no_save(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(contracts_mod, "save_contract_store", lambda store, root: root)


@pytest.fixture
def extracted(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Record which repo aliases actually ran extraction.

    ``run_contract_extraction`` imports ``iter_source_files`` at call time, so
    patching it on the defining module is what the running code sees.
    """
    seen: list[str] = []

    from repowise.core.workspace.extractors import base

    original_iter = base.iter_source_files

    def recording_iter(repo_path, wanted, exclude=None):
        seen.append(Path(repo_path).name)
        return original_iter(repo_path, wanted, exclude)

    monkeypatch.setattr(base, "iter_source_files", recording_iter)
    return seen


async def _first_run(workspace: WorkspaceConfig, root: Path) -> ContractStore:
    """A full extraction, producing the store a later run may carry forward."""
    return await run_contract_extraction(workspace, root, [])


# ---------------------------------------------------------------------------
# Scoping actually happens
# ---------------------------------------------------------------------------


async def test_unchanged_repo_is_not_re_extracted(
    workspace: WorkspaceConfig, tmp_path: Path, no_save: None, extracted: list[str]
) -> None:
    first = await _first_run(workspace, tmp_path)
    extracted.clear()

    await run_contract_extraction(workspace, tmp_path, ["alpha"], None, first)

    assert extracted == ["alpha"], "beta was unchanged and should not have been walked"


async def test_carried_forward_contracts_are_identical(
    workspace: WorkspaceConfig, tmp_path: Path, no_save: None
) -> None:
    first = await _first_run(workspace, tmp_path)
    second = await run_contract_extraction(workspace, tmp_path, ["alpha"], None, first)

    def ids(store: ContractStore, alias: str) -> set[str]:
        return {c.contract_id for c in store.rows_for_repo(alias)}

    assert ids(second, "beta") == ids(first, "beta")
    assert ids(second, "alpha") == ids(first, "alpha")


async def test_no_previous_store_means_everything_is_extracted(
    workspace: WorkspaceConfig, tmp_path: Path, no_save: None, extracted: list[str]
) -> None:
    """The default, and what every existing caller and test relies on."""
    await run_contract_extraction(workspace, tmp_path, ["alpha"])
    assert sorted(extracted) == ["alpha", "beta"]


# ---------------------------------------------------------------------------
# Reuse is validated, not trusted
# ---------------------------------------------------------------------------


async def test_moved_head_is_re_extracted_even_when_not_in_changed_repos(
    workspace: WorkspaceConfig, tmp_path: Path, no_save: None, extracted: list[str]
) -> None:
    """The whole point of the HEAD check.

    ``changed_repos`` says beta is clean. Beta has in fact moved — a crashed
    previous run, a branch switch, a hook that fired on a partial set. The
    persisted sha disagrees, so beta is re-extracted rather than carried
    forward stale.
    """
    first = await _first_run(workspace, tmp_path)
    beta = tmp_path / "beta"
    (beta / "svc" / "extra.py").write_text('@router.get("/added")\ndef added(): ...\n', encoding="utf-8")
    _git(beta, "add", "-A")
    _git(beta, "commit", "-q", "-m", "second")
    extracted.clear()

    await run_contract_extraction(workspace, tmp_path, ["alpha"], None, first)

    assert sorted(extracted) == ["alpha", "beta"]


async def test_missing_provenance_forces_re_extraction(
    workspace: WorkspaceConfig, tmp_path: Path, no_save: None, extracted: list[str]
) -> None:
    """An artifact written before provenance existed cannot prove freshness."""
    first = await _first_run(workspace, tmp_path)
    first.repo_provenance.pop("beta")
    extracted.clear()

    await run_contract_extraction(workspace, tmp_path, ["alpha"], None, first)

    assert sorted(extracted) == ["alpha", "beta"]


async def test_unreadable_head_forces_re_extraction(
    workspace: WorkspaceConfig, tmp_path: Path, no_save: None, extracted: list[str]
) -> None:
    """A repo that is not a git checkout can never be proven unchanged."""
    first = await _first_run(workspace, tmp_path)
    extracted.clear()

    import repowise.core.workspace.update as update_mod

    original = update_mod.get_head_commit

    def no_head(repo_path):
        return None if Path(repo_path).name == "beta" else original(repo_path)

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(update_mod, "get_head_commit", no_head)
        await run_contract_extraction(workspace, tmp_path, ["alpha"], None, first)

    assert sorted(extracted) == ["alpha", "beta"]


async def test_dirty_working_tree_forces_re_extraction(
    workspace: WorkspaceConfig, tmp_path: Path, no_save: None, extracted: list[str]
) -> None:
    """HEAD is not enough on its own.

    Extraction reads the working tree, so an uncommitted route change alters
    what it would find while leaving HEAD exactly where the stamp says it is.
    Trusting the sha alone would hide that edit until something else happened
    to commit.
    """
    first = await _first_run(workspace, tmp_path)
    (tmp_path / "beta" / "svc" / "api.py").write_text(
        'router = APIRouter()\n\n\n@router.get("/uncommitted")\ndef added(): ...\n',
        encoding="utf-8",
    )
    extracted.clear()

    second = await run_contract_extraction(workspace, tmp_path, ["alpha"], None, first)

    assert sorted(extracted) == ["alpha", "beta"]
    assert any("uncommitted" in c.contract_id for c in second.rows_for_repo("beta"))


async def test_changed_contract_config_forces_re_extraction(
    workspace: WorkspaceConfig, tmp_path: Path, no_save: None, extracted: list[str]
) -> None:
    """Rows extracted under a different question must not be reused.

    Turning a detector off, or adding a ``service_bases`` mapping, changes what
    extraction looks for and how consumers resolve — but it moves no commit, so
    the HEAD check alone would happily carry the old answers forward and the
    config change would appear to do nothing until someone committed.
    """
    first = await _first_run(workspace, tmp_path)
    extracted.clear()

    narrowed = WorkspaceConfig(
        repos=list(workspace.repos),
        contracts=ContractConfig(detect_data=False),
    )
    second = await run_contract_extraction(narrowed, tmp_path, [], None, first)

    assert sorted(extracted) == ["alpha", "beta"]
    assert not [c for c in second.contracts if c.contract_type == "data"]


async def test_same_config_still_reuses(
    workspace: WorkspaceConfig, tmp_path: Path, no_save: None, extracted: list[str]
) -> None:
    """The fingerprint must be stable across runs, or nothing is ever reused."""
    first = await _first_run(workspace, tmp_path)
    extracted.clear()

    await run_contract_extraction(workspace, tmp_path, [], None, first)

    assert extracted == []


# ---------------------------------------------------------------------------
# Deletion — the silent-staleness half
# ---------------------------------------------------------------------------


async def test_repo_removed_from_config_leaves_no_phantom_rows(
    workspace: WorkspaceConfig, tmp_path: Path, no_save: None
) -> None:
    """A dropped repo takes its contracts, stats and provenance with it.

    Carrying them forward is how a workspace grows providers that no code
    declares. The merged set is built from the current repo list, so a repo that
    is gone is never visited and cannot contribute.
    """
    for alias in ("gamma",):
        _make_git_repo(tmp_path / alias, alias)
    three = WorkspaceConfig(
        repos=[
            RepoEntry(path="alpha", alias="alpha"),
            RepoEntry(path="beta", alias="beta"),
            RepoEntry(path="gamma", alias="gamma"),
        ],
        contracts=ContractConfig(),
    )
    first = await run_contract_extraction(three, tmp_path, [])
    assert first.rows_for_repo("gamma"), "fixture should produce gamma contracts"

    second = await run_contract_extraction(workspace, tmp_path, ["alpha"], None, first)

    assert second.rows_for_repo("gamma") == []
    assert "gamma" not in second.extraction_stats
    assert "gamma" not in second.repo_provenance


async def test_unindexed_repo_leaves_no_phantom_rows(
    workspace: WorkspaceConfig, tmp_path: Path, no_save: None
) -> None:
    """Losing ``.repowise/`` is the same deletion by a different route."""
    first = await _first_run(workspace, tmp_path)
    assert first.rows_for_repo("beta")

    for alias in ("gamma", "delta"):
        _make_git_repo(tmp_path / alias, alias)
    (tmp_path / "beta" / ".repowise").rmdir()
    wider = WorkspaceConfig(
        repos=[
            RepoEntry(path="alpha", alias="alpha"),
            RepoEntry(path="beta", alias="beta"),
            RepoEntry(path="gamma", alias="gamma"),
            RepoEntry(path="delta", alias="delta"),
        ],
        contracts=ContractConfig(),
    )

    second = await run_contract_extraction(wider, tmp_path, ["alpha"], None, first)

    assert second.rows_for_repo("beta") == []
    assert "beta" not in second.repo_provenance


# ---------------------------------------------------------------------------
# Stats and contracts move as one unit
# ---------------------------------------------------------------------------


async def test_stats_and_contracts_are_carried_forward_together(
    workspace: WorkspaceConfig, tmp_path: Path, no_save: None
) -> None:
    """The coverage ratio's numerator and denominator stay commit-aligned.

    If a repo's contracts were reused while its stats were dropped (or vice
    versa), ``build_diagnostics`` would divide a count of consumers from one
    commit by an unresolved count from another and report a coverage percentage
    that describes no run that ever happened.
    """
    first = await _first_run(workspace, tmp_path)
    first.extraction_stats["beta"] = {"http_consumer_unresolved": 7, "files_walked": 3, "walks": 1}

    second = await run_contract_extraction(workspace, tmp_path, ["alpha"], None, first)

    assert second.extraction_stats["beta"]["http_consumer_unresolved"] == 7
    assert second.rows_for_repo("beta") == first.rows_for_repo("beta")
    assert second.repo_provenance["beta"] == first.repo_provenance["beta"]


async def test_re_extracted_repo_gets_fresh_stats_and_stamp(
    workspace: WorkspaceConfig, tmp_path: Path, no_save: None
) -> None:
    first = await _first_run(workspace, tmp_path)
    first.extraction_stats["alpha"] = {"http_consumer_unresolved": 99}

    second = await run_contract_extraction(workspace, tmp_path, ["alpha"], None, first)

    assert second.extraction_stats["alpha"].get("http_consumer_unresolved", 0) != 99
    assert second.repo_provenance["alpha"]["extracted_at"] == second.generated_at
    assert second.repo_provenance["beta"]["extracted_at"] != second.generated_at


async def test_provenance_records_the_commit_each_repo_describes(
    workspace: WorkspaceConfig, tmp_path: Path, no_save: None
) -> None:
    first = await _first_run(workspace, tmp_path)
    for alias in ("alpha", "beta"):
        assert first.repo_provenance[alias]["head"] == _git(tmp_path / alias, "rev-parse", "HEAD")


async def test_provenance_round_trips_through_json(
    workspace: WorkspaceConfig, tmp_path: Path, no_save: None
) -> None:
    first = await _first_run(workspace, tmp_path)
    revived = ContractStore.from_dict(first.to_dict())
    assert revived.repo_provenance == first.repo_provenance
    assert revived.extraction_stats == first.extraction_stats


async def test_legacy_artifact_without_provenance_loads(tmp_path: Path) -> None:
    """Reading an artifact from before this field existed must not raise."""
    store = ContractStore.from_dict(
        {"version": 1, "generated_at": "2026-01-01T00:00:00Z", "contracts": [], "contract_links": []}
    )
    assert store.repo_provenance == {}


# ---------------------------------------------------------------------------
# Links are recomputed globally, never merged
# ---------------------------------------------------------------------------


async def test_a_new_provider_links_a_carried_forward_consumer(
    tmp_path: Path, no_save: None
) -> None:
    """The reason matching is never scoped.

    Beta's consumer is carried forward untouched. Alpha gains the provider that
    satisfies it. If matching ran only over the changed repo's contracts, or if
    beta's old (unmatched) links were merged forward, this link would not exist.
    """
    alpha = tmp_path / "alpha"
    beta = tmp_path / "beta"
    alpha.mkdir()
    (alpha / ".repowise").mkdir()
    (alpha / "placeholder.py").write_text("x = 1\n", encoding="utf-8")
    beta.mkdir()
    (beta / ".repowise").mkdir()
    (beta / "client.ts").write_text("await fetch('/api/orders');\n", encoding="utf-8")
    for repo in (alpha, beta):
        _git(repo, "init", "-q")
        _git(repo, "config", "user.email", "test@example.com")
        _git(repo, "config", "user.name", "Test")
        _git(repo, "add", "-A")
        _git(repo, "commit", "-q", "-m", "initial")

    config = WorkspaceConfig(
        repos=[RepoEntry(path="alpha", alias="alpha"), RepoEntry(path="beta", alias="beta")],
        contracts=ContractConfig(),
    )
    first = await run_contract_extraction(config, tmp_path, [])
    assert not [lk for lk in first.contract_links if lk.contract_id.startswith("http::GET::/api/orders")]

    (alpha / "api.py").write_text(
        'router = APIRouter()\n\n\n@router.get("/api/orders")\ndef orders(): ...\n', encoding="utf-8"
    )
    _git(alpha, "add", "-A")
    _git(alpha, "commit", "-q", "-m", "add provider")

    second = await run_contract_extraction(config, tmp_path, ["alpha"], None, first)

    linked = [lk for lk in second.contract_links if "orders" in lk.contract_id]
    assert linked, "a carried-forward consumer must match a newly extracted provider"
    assert linked[0].consumer_repo == "beta"
    assert linked[0].provider_repo == "alpha"


async def test_carried_consumer_drops_a_target_that_left_the_workspace(
    workspace: WorkspaceConfig, tmp_path: Path, no_save: None
) -> None:
    """Annotation is recomputed for carried rows, not inherited.

    ``annotate_consumer_targets`` only ever wrote its keys; nothing cleared
    them. That was invisible while every contract was re-extracted from source
    each run, because ``meta`` started fresh. Once a consumer is carried
    forward, an old ``target_repo`` naming a repo that has since left the
    workspace would survive and the contract would keep claiming a call into it.
    """
    first = await _first_run(workspace, tmp_path)
    # Must be a consumer of the repo that gets CARRIED FORWARD. Staleness on a
    # re-extracted repo is impossible by construction — its contracts are new
    # objects — so injecting there would assert nothing.
    carried = [
        c
        for c in first.rows_for_repo("beta")
        if c.role == "consumer" and c.contract_type == "http"
    ]
    assert carried, "fixture must give beta an HTTP consumer or this asserts nothing"
    carried[0].meta["target_repo"] = "gamma_that_is_gone"

    second = await run_contract_extraction(workspace, tmp_path, ["alpha"], None, first)

    stale = [
        c
        for c in second.contracts
        if c.meta.get("target_repo") == "gamma_that_is_gone"
    ]
    assert not stale, "a carried-forward consumer kept a target repo that no longer exists"


# ---------------------------------------------------------------------------
# Walk budget under incremental extraction
# ---------------------------------------------------------------------------


async def test_walk_count_scales_with_changed_repos_not_workspace_size(
    workspace: WorkspaceConfig, tmp_path: Path, no_save: None, extracted: list[str]
) -> None:
    """The counter that makes the win visible in the artifact.

    ``files_walked`` is per repo, so the workspace total after a one-repo commit
    is that repo's file count — not the sum over every repo. A regression that
    quietly reintroduced the full rescan would move this number.
    """
    first = await _first_run(workspace, tmp_path)
    extracted.clear()

    second = await run_contract_extraction(workspace, tmp_path, ["alpha"], None, first)

    # The oracle is what was actually walked, not a number the store carries:
    # every per-repo counter in extraction_stats survives carry-forward, so a
    # full rescan and an incremental run leave the same totals behind and no
    # assertion over them can tell the two apart.
    assert extracted == ["alpha"]
    walked_this_run = sum(
        second.extraction_stats[a].get("files_walked", 0)
        for a, p in second.repo_provenance.items()
        if p.get("extracted_at") == second.generated_at
    )
    full_total = sum(s.get("files_walked", 0) for s in first.extraction_stats.values())
    assert 0 < walked_this_run < full_total
    assert second.extraction_stats["alpha"]["walks"] == 1  # still one walk per repo
