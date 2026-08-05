"""The get_answer contradiction guard.

The reproduction these tests exist for: asked whether to run the declared
formatter before committing, ``get_answer`` reads a ``format`` target out of
the build files and answers yes, at ``confidence: medium``, while the
checkout's own episode store holds a ``formatter_drift`` record saying the
tree is not formatter-clean. The guard appends the record beside the answer.

The anti-reproductions matter more than the reproduction: a guard that fires
when it should not is the failure this feature exists to prevent, so most of
what follows asserts a **byte-identical** payload.
"""

from __future__ import annotations

import copy
import subprocess
import time

import pytest

from repowise.core.precedent.store import (
    TIER_STRUCTURAL,
    Episode,
    EpisodeStore,
    default_store_path,
)
from repowise.server.mcp_server.tool_answer.episodes import attach_episode_sync

FORMATTER_ANSWER = (
    "**Yes — run `ruff format` before committing.** This repo defines a "
    "`format` target, which usually means CI will fail if files are not "
    "already formatted."
)


# -- fixtures ----------------------------------------------------------------


@pytest.fixture
def repo(tmp_path):
    """A git checkout with a .repowise directory, one commit deep."""
    root = tmp_path / "checkout"
    (root / ".repowise").mkdir(parents=True)
    (root / "pyproject.toml").write_text("[project]\nname = 'demo'\n", encoding="utf-8")
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "t@example.com")
    _git(root, "config", "user.name", "T")
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "first")
    return root


def _git(root, *args):
    subprocess.run(["git", *args], cwd=str(root), check=True, capture_output=True)


def _head(root) -> str:
    out = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=str(root), capture_output=True, text=True, check=True
    )
    return out.stdout.strip()


def _write(root, episode: Episode, *, born: float, seen: float | None = None):
    """Persist *episode* with an explicit birth/last-seen pair.

    ``replace_kinds`` stamps both from one clock, which is exactly the
    "derived once, never re-observed" state; a later re-observation is
    simulated by moving ``last_seen_at`` forward on its own.
    """
    with EpisodeStore(default_store_path(root)) as store:
        store.replace_kinds(
            tier=episode.tier, kinds=[episode.kind], episodes=[episode], now=born
        )
        if seen is not None:
            # Reaches past the write API on purpose: `replace_kinds` stamps
            # birth and last-seen from one clock, so a later re-observation
            # has no public expression.
            store._conn.execute(
                "UPDATE episodes SET last_seen_at = ? WHERE id = ?", (seen, episode.id)
            )
            store._conn.commit()


def formatter_episode(birth_commit: str | None) -> Episode:
    return Episode(
        tier=TIER_STRUCTURAL,
        kind="formatter_drift",
        subject="ruff format",
        body="This tree is not formatter-clean: 419 files would be reformatted.",
        evidence="ruff format --check .: 419 files would be reformatted",
        nodes=(),
        birth_commit=birth_commit,
    )


def payload(**over) -> dict:
    base = {
        "answer": FORMATTER_ANSWER,
        "citations": ["Makefile"],
        "confidence": "medium",
        "retrieval_quality": "weak",
        "fallback_targets": ["pyproject.toml"],
        "retrieval": [],
    }
    base.update(over)
    return base


def run(p, repo, *, question="should I run ruff format before committing", name="demo"):
    attach_episode_sync(p, question=question, repo_path=repo, repo_name=name)
    return p


# -- the reproduction --------------------------------------------------------


def test_formatter_episode_appears_beside_the_answer(repo):
    _write(repo, formatter_episode(_head(repo)), born=time.time())

    got = run(payload(), repo)

    assert len(got["episodes"]) == 1
    episode = got["episodes"][0]
    assert episode["kind"] == "formatter_drift"
    assert "419 files" in episode["recorded"]
    assert episode["scope"] == "the checkout as a whole"
    # Add, never replace: the synthesis is untouched.
    assert got["answer"] == FORMATTER_ANSWER
    assert got["confidence"] == "medium"
    # It arrives as dated evidence, not as an instruction.
    assert "not a correction" in got["note"]


def test_repo_wide_episode_survives_the_tree_moving_on(repo):
    """A commit does not falsify "the tree is not formatter-clean".

    Its scope is the whole tree, so any commit makes ``git rev-list --count``
    non-zero. Suppressing on that retires the record on the very next commit
    and leaves a guard that only passes its own tests, so the age labels
    rather than suppresses.
    """
    born_at = _head(repo)
    _write(repo, formatter_episode(born_at), born=time.time())
    (repo / "later.txt").write_text("x", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "second")

    got = run(payload(), repo)

    assert got["episodes"][0]["still_true"] == (
        f"recorded {time.strftime('%Y-%m-%d', time.gmtime())} at {born_at[:12]}; "
        "the tree has moved 1 commit since and this was not re-checked"
    )


# -- the anti-reproductions --------------------------------------------------


def test_high_confidence_is_byte_identical(repo):
    _write(repo, formatter_episode(_head(repo)), born=time.time())
    before = payload(confidence="high")

    assert run(copy.deepcopy(before), repo) == before


def test_an_empty_store_changes_nothing(repo):
    before = payload()

    assert run(copy.deepcopy(before), repo) == before


def test_no_store_at_all_changes_nothing_and_creates_nothing(tmp_path):
    """A repo that never derived episodes must not grow a database."""
    root = tmp_path / "bare"
    root.mkdir()
    before = payload()
    after = copy.deepcopy(before)

    attach_episode_sync(after, question="ruff format?", repo_path=root, repo_name="bare")

    assert after == before
    assert not default_store_path(root).exists()


def test_an_unrelated_question_changes_nothing(repo):
    """Scope is the whole filter; an empty node set is not a wildcard."""
    _write(repo, formatter_episode(_head(repo)), born=time.time())
    before = payload(
        answer="`_serialize_hits` trims each hit to a summary.",
        citations=["answer.py"],
        fallback_targets=[],
    )

    got = run(copy.deepcopy(before), repo, question="how does _serialize_hits work")

    assert got == before


def test_a_node_scoped_episode_is_suppressed_once_its_scope_changes(repo):
    """Where git *can* decide, precondition 2 stays absolute."""
    (repo / "backend").mkdir()
    (repo / "backend" / "main.py").write_text("x = 1\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "add backend")
    born_at = _head(repo)
    _write(
        repo,
        Episode(
            tier=TIER_STRUCTURAL,
            kind="nested_repos",
            subject=".",
            body="backend is a separate git repository.",
            evidence="walk stopped at a nested .git boundary",
            nodes=("backend",),
            birth_commit=born_at,
        ),
        born=time.time(),
    )
    asked = payload(answer="Routers are registered in backend/main.py.", citations=["backend/main.py"])

    # Nothing in scope has changed yet, so it is served.
    served = run(copy.deepcopy(asked), repo, question="where are routers registered")
    assert served["episodes"][0]["scope"] == ["backend/main.py"]

    # Touch the scope, and it goes quiet rather than vouching for itself.
    (repo / "backend" / "main.py").write_text("x = 2\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "touch backend")

    assert run(copy.deepcopy(asked), repo, question="where are routers registered") == asked


def test_a_re_observed_episode_needs_no_git_query(repo):
    """``last_seen_at > birth_at`` is proof of currency that costs nothing."""
    now = time.time()
    _write(repo, formatter_episode(_head(repo)), born=now, seen=now + 60)

    got = run(payload(), repo)

    assert got["episodes"][0]["still_true"] == (
        f"re-observed by a later index (recorded {time.strftime('%Y-%m-%d', time.gmtime(now))})"
    )


def test_without_git_a_repo_wide_fact_is_served_and_a_scoped_one_is_not(tmp_path):
    """An index with no git checkout (a tarball, a hosted job) still degrades.

    ``git rev-list`` cannot answer either question here. The repo-wide fact
    says so and is served; the node-scoped one cannot be vouched for and goes
    quiet, which is the asymmetry precondition 2 encodes.
    """
    root = tmp_path / "nogit"
    (root / ".repowise").mkdir(parents=True)
    born = time.time()
    _write(root, formatter_episode("deadbeef" * 5), born=born)
    _write(
        root,
        Episode(
            tier=TIER_STRUCTURAL,
            kind="nested_repos",
            subject="vendor",
            body="vendor is a separate git repository.",
            evidence="walk stopped at a nested .git boundary",
            nodes=("vendor",),
            birth_commit="deadbeef" * 5,
        ),
        born=born,
    )

    wide = payload()
    attach_episode_sync(
        wide, question="should I run ruff format?", repo_path=root, repo_name="nogit"
    )
    assert wide["episodes"][0]["kind"] == "formatter_drift"
    assert "not re-checked since" in wide["episodes"][0]["still_true"]

    scoped = payload(answer="It lives in vendor/lib.py.", citations=["vendor/lib.py"])
    before = copy.deepcopy(scoped)
    attach_episode_sync(
        scoped, question="where does lib live", repo_path=root, repo_name="nogit"
    )
    assert scoped == before


# -- subject scope -----------------------------------------------------------


def test_a_subject_equal_to_the_repo_name_has_no_topic_scope(repo):
    """Every answer in a repo names the repo, so matching on it matches nothing."""
    _write(
        repo,
        Episode(
            tier=TIER_STRUCTURAL,
            kind="editable_shadow",
            subject="demo",
            body="`demo` is installed as a console script beside an editable install.",
            evidence="Scripts/demo.exe beside a .pth",
            nodes=(),
        ),
        born=time.time(),
    )
    before = payload(answer="Run `demo update` to refresh the demo index.", fallback_targets=[])

    got = run(copy.deepcopy(before), repo, question="how do I refresh demo", name="demo")

    assert got == before


def test_a_rare_launcher_name_is_matched(repo):
    _write(
        repo,
        Episode(
            tier=TIER_STRUCTURAL,
            kind="editable_shadow",
            subject="demo-augment",
            body="`demo-augment` is installed as a console script beside an editable install.",
            evidence="Scripts/demo-augment.exe beside a .pth",
            nodes=(),
        ),
        born=time.time(),
    )

    got = run(
        payload(answer="The hook entry point is the `demo-augment` console script."),
        repo,
        question="what runs demo-augment",
        name="demo",
    )

    assert got["episodes"][0]["subject"] == "demo-augment"


def test_a_subject_matches_as_a_phrase_not_a_substring(repo):
    _write(repo, formatter_episode(_head(repo)), born=time.time())
    before = payload(
        answer="The `ruff formatter` docs describe its style.", fallback_targets=[]
    )

    got = run(copy.deepcopy(before), repo, question="what is ruff formatting")

    assert got == before


# -- budget ------------------------------------------------------------------


def test_a_long_body_is_capped_and_stays_recoverable(repo):
    """A cap on a count is not a bound on a response whose fields are free text."""
    long_body = "ruff drift. " * 400
    _write(
        repo,
        Episode(
            tier=TIER_STRUCTURAL,
            kind="formatter_drift",
            subject="ruff format",
            body=long_body,
            evidence="ruff format --check .",
            nodes=(),
            birth_commit=_head(repo),
        ),
        born=time.time(),
    )

    got = run(payload(), repo)

    recorded = got["episodes"][0]["recorded"]
    assert len(recorded) < len(long_body)
    assert "repowise#" in recorded  # the drop is recoverable, not silent
    assert got["_meta"]["omitted"]["refs"]


# -- the cached path, which is the trap --------------------------------------
#
# A cache hit returns long before the fresh path's payload is ever built. The
# guard therefore runs at serve time on BOTH paths: applied before the cache
# write instead, a disagreement would be frozen into the row and served after
# the episode had been superseded.

# Deliberately non-dominant (no gap), which is what holds the answer below
# ``high`` — the exact calibration the live reproduction returns.
_HITS = [
    {"page_id": "file_page:src/auth/service.py", "score": 3.0},
    {"page_id": "file_page:src/auth/middleware.py", "score": 2.9},
]
CACHE_QUESTION = "should I run ruff format before committing"


def _patch_retrieval(monkeypatch, answer_mod):
    async def _fake_retrieve(question, ctx):
        return [dict(h) for h in _HITS]

    async def _fake_hydrate(hits, ctx, *, scope=None):
        for h in hits:
            h["target_path"] = h["page_id"].removeprefix("file_page:")
            h["title"] = h["target_path"]
            h["summary"] = h["snippet"] = ""
            h["page_type"] = "file_page"
        return hits

    monkeypatch.setattr(answer_mod, "_hybrid_retrieve", _fake_retrieve)
    monkeypatch.setattr(answer_mod, "_hydrate_hits", _fake_hydrate)


class _Provider:
    provider_name = "mock"
    model_name = "mock-1"

    def __init__(self, content: str) -> None:
        self.content = content
        self.calls = 0

    async def generate(self, **kwargs):
        self.calls += 1
        from types import SimpleNamespace

        return SimpleNamespace(content=self.content)


@pytest.mark.asyncio
async def test_the_episode_is_served_on_both_paths_and_never_cached(
    setup_mcp, factory, monkeypatch, repo
):
    import json as _json

    from sqlalchemy import select

    import repowise.server.mcp_server as mcp_mod
    import repowise.server.mcp_server.tool_answer.answer as answer_mod
    from repowise.core.persistence.database import get_session
    from repowise.core.persistence.models import AnswerCache
    from repowise.server.mcp_server import get_answer

    _write(repo, formatter_episode(_head(repo)), born=time.time())
    monkeypatch.setattr(mcp_mod, "_repo_path", str(repo))
    _patch_retrieval(monkeypatch, answer_mod)
    provider = _Provider("Yes, run `ruff format` before committing to satisfy the format target.")
    monkeypatch.setattr(answer_mod, "_resolve_provider_for_answer", lambda _p: provider)

    fresh = await get_answer(CACHE_QUESTION)
    assert fresh["confidence"] != "high"
    assert fresh["episodes"][0]["kind"] == "formatter_drift"

    # The cache row is the pre-episode payload: nothing to freeze, and nothing
    # that would need an _ANSWER_SCHEMA_VERSION bump to invalidate.
    async with get_session(factory) as session:
        rows = list((await session.execute(select(AnswerCache))).scalars().all())
    assert len(rows) == 1
    assert "episodes" not in _json.loads(rows[0].payload_json)

    cached = await get_answer(CACHE_QUESTION)
    assert provider.calls == 1, "second call must be served from cache"
    assert cached["_meta"]["cached"] is True
    assert cached["episodes"][0]["kind"] == "formatter_drift"
