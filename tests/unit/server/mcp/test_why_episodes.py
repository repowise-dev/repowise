"""Episodes as an evidence kind, and as a count.

Two surfaces over one store. ``get_why`` serves the bodies because the reader
asked a question; ``get_risk`` and ``get_context`` serve a single integer
because the reader asked for a card and a paragraph would spend their budget
whether they wanted it or not.

The divergence worth holding onto is currency. The ``get_answer`` guard
suppresses an episode whose scope has moved, because it is appending a claim
beside an answer about the present. These tests assert the opposite for
``get_why``: the episode is served with the movement **labelled**, because
"what happened here" is a question about the past and a superseded episode is
still a true answer to it.
"""

from __future__ import annotations

import subprocess

import pytest

from repowise.core.precedent.store import (
    TIER_GIT,
    TIER_STRUCTURAL,
    TIER_TRANSCRIPT,
    Episode,
    EpisodeStore,
    default_store_path,
)
from repowise.server.mcp_server._episodes import (
    _MAX_EVIDENCE_BODY_CHARS,
    _MAX_SCOPE_NODES,
    bank_overflow,
    currency,
    enrich_episode_counts,
    episode_counts,
    episode_evidence,
)


@pytest.fixture
def repo(tmp_path):
    """A git checkout with a .repowise directory, one commit deep."""
    root = tmp_path / "checkout"
    (root / ".repowise").mkdir(parents=True)
    (root / "app.py").write_text("x = 1\n", encoding="utf-8")
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


def _fix(subject: str, nodes: tuple[str, ...], *, birth_commit: str, body: str = "a fix") -> Episode:
    return Episode(
        tier=TIER_GIT,
        kind="code_fix",
        subject=subject,
        body=body,
        evidence=f"commit {subject}",
        nodes=nodes,
        birth_commit=birth_commit,
        birth_at=1000.0,
    )


def _write(root, *episodes: Episode, tier: str = TIER_GIT) -> None:
    with EpisodeStore(default_store_path(root)) as store:
        store.append_tier(tier=tier, episodes=list(episodes), oldest_birth_at=None)


# -- the evidence block ------------------------------------------------------


class TestEpisodeEvidence:
    def test_an_episode_bound_to_the_path_is_served(self, repo):
        _write(repo, _fix("sha1", ("app.py",), birth_commit=_head(repo)))
        entries, _ = episode_evidence(repo, paths=["app.py"])
        assert len(entries) == 1
        assert entries[0]["kind"] == "code_fix"
        assert entries[0]["recorded"] == "a fix"
        assert entries[0]["scope"] == ["app.py"]

    def test_a_path_with_no_episodes_says_nothing(self, repo):
        _write(repo, _fix("sha1", ("app.py",), birth_commit=_head(repo)))
        entries, pending = episode_evidence(repo, paths=["other.py"])
        assert entries == []
        assert pending == []

    def test_a_repo_that_never_derived_episodes_is_silent(self, tmp_path):
        """And must not grow a store because somebody asked a question."""
        root = tmp_path / "bare"
        (root / ".repowise").mkdir(parents=True)
        entries, _ = episode_evidence(root, paths=["app.py"])
        assert entries == []
        assert not default_store_path(root).exists()

    def test_asking_nothing_reads_nothing(self, repo):
        assert episode_evidence(repo) == ([], [])

    def test_a_transcript_episode_is_never_served(self, repo):
        """Per-machine: two people asking one question would get two answers."""
        _write(
            repo,
            Episode(
                tier=TIER_TRANSCRIPT,
                kind="session",
                subject="s",
                body="a session",
                evidence="transcript",
                nodes=("app.py",),
                birth_at=1000.0,
            ),
            tier=TIER_TRANSCRIPT,
        )
        entries, _ = episode_evidence(repo, paths=["app.py"])
        assert entries == []

    def test_the_block_is_bounded(self, repo):
        _write(
            repo,
            *[_fix(f"sha{i}", ("app.py",), birth_commit=_head(repo)) for i in range(10)],
        )
        entries, _ = episode_evidence(repo, paths=["app.py"])
        assert len(entries) == 3

    def test_a_long_body_is_capped_and_stays_recoverable(self, repo):
        """A cap on a count is not a bound on a response whose fields are free text."""
        _write(repo, _fix("sha1", ("app.py",), birth_commit=_head(repo), body="x" * 5000))
        entries, pending = episode_evidence(repo, paths=["app.py"])
        collector = bank_overflow(pending, tool="get_why", repo_root=repo)
        recorded = entries[0]["recorded"]
        # The cap plus the recovery marker, which is ~95 chars and is the
        # point: what was cut is advertised rather than silently dropped.
        assert recorded.startswith("x" * _MAX_EVIDENCE_BODY_CHARS)
        assert len(recorded) < _MAX_EVIDENCE_BODY_CHARS + 150
        assert "repowise expand" in recorded
        assert collector is not None
        payload: dict = {}
        collector.attach(payload)
        assert payload["_meta"]["omitted"]["refs"]

    def test_one_collector_serves_every_capped_body(self, repo):
        """Two collectors on one response and the second clobbers the first's refs."""
        _write(
            repo,
            *[
                _fix(f"sha{i}", ("app.py",), birth_commit=_head(repo), body="x" * 5000)
                for i in range(3)
            ],
        )
        entries, pending = episode_evidence(repo, paths=["app.py"])
        collector = bank_overflow(pending, tool="get_why", repo_root=repo)
        assert len(entries) == 3
        payload: dict = {}
        collector.attach(payload)
        # Three identical bodies are one stored row, so one ref, counted once.
        assert len(payload["_meta"]["omitted"]["refs"]) == 1

    def test_a_query_ranks_bodies_when_there_is_no_path(self, repo):
        _write(
            repo,
            _fix("sha1", ("app.py",), birth_commit=_head(repo), body="fixed the parser crash"),
            _fix("sha2", ("other.py",), birth_commit=_head(repo), body="bumped a dependency"),
        )
        entries, _ = episode_evidence(repo, query="parser crash")
        assert entries
        assert "parser" in entries[0]["recorded"]


# -- currency ----------------------------------------------------------------


class TestCurrency:
    def test_an_untouched_scope_is_current(self, repo):
        _write(repo, _fix("sha1", ("app.py",), birth_commit=_head(repo)))
        entries, _ = episode_evidence(repo, paths=["app.py"])
        assert "nothing in its scope has changed" in entries[0]["still_true"]

    def test_a_moved_scope_is_labelled_rather_than_suppressed(self, repo):
        """The divergence from the get_answer guard, and the reason for it.

        A fix that landed and was later changed is exactly the history the
        question asked for. Suppressing it would answer a different question.
        """
        born = _head(repo)
        (repo / "app.py").write_text("x = 2\n", encoding="utf-8")
        _git(repo, "add", "-A")
        _git(repo, "commit", "-qm", "second")
        _write(repo, _fix("sha1", ("app.py",), birth_commit=born))

        entries, _ = episode_evidence(repo, paths=["app.py"])
        assert len(entries) == 1
        assert "1 commit has touched its scope since" in entries[0]["still_true"]

    def test_the_gate_form_still_suppresses_a_moved_scope(self, repo):
        """get_answer's contract is unchanged by get_why's.

        The two share one implementation, so this is the test that keeps the
        shared half from being tuned for whichever caller was edited last.
        """
        from repowise.server.mcp_server._episodes import still_true

        born = _head(repo)
        (repo / "app.py").write_text("x = 2\n", encoding="utf-8")
        _git(repo, "add", "-A")
        _git(repo, "commit", "-qm", "second")
        row = {
            "tier": TIER_GIT,
            "nodes": ["app.py"],
            "birth_commit": born,
            "birth_at": 1000.0,
            "last_seen_at": 1000.0,
        }
        assert currency(row, root=repo).current is False
        assert still_true(row, root=repo) is None

    def test_only_the_top_ranked_episode_costs_a_git_query(self, repo, monkeypatch):
        """~60 ms each, and the reader acts on the first one.

        The same bound, for the same reason, that this mode already applies to
        the decision it ranks first.
        """
        from repowise.core.precedent import currency as mod

        calls: list = []
        real = mod.commits_since

        def counted(*args, **kwargs):
            calls.append(args)
            return real(*args, **kwargs)

        monkeypatch.setattr(mod, "commits_since", counted)
        _write(
            repo,
            *[_fix(f"sha{i}", ("app.py",), birth_commit=_head(repo)) for i in range(5)],
        )
        entries, _ = episode_evidence(repo, paths=["app.py"])
        assert len(entries) == 3
        assert len(calls) == 1

    def test_a_re_observed_structural_fact_needs_no_git_query(self, repo, monkeypatch):
        from repowise.core.precedent import currency as mod

        calls: list = []
        monkeypatch.setattr(mod, "commits_since", lambda *a, **k: calls.append(a))
        with EpisodeStore(default_store_path(repo)) as store:
            ep = Episode(
                tier=TIER_STRUCTURAL,
                kind="nested_repos",
                subject=".",
                body="8 directories are separate repos",
                evidence="traverser",
                nodes=("app.py",),
            )
            store.replace_kinds(tier=TIER_STRUCTURAL, kinds=["nested_repos"], episodes=[ep],
                                now=1000.0)
            store._conn.execute("UPDATE episodes SET last_seen_at = ? WHERE id = ?",
                                (2000.0, ep.id))
            store._conn.commit()

        entries, _ = episode_evidence(repo, paths=["app.py"])
        assert "re-observed by a later index" in entries[0]["still_true"]
        assert calls == []

    def test_a_lower_ranked_episode_makes_no_currency_claim(self, repo):
        """An absent field beats a guess dressed as a verdict."""
        _write(
            repo,
            *[_fix(f"sha{i}", ("app.py",), birth_commit=_head(repo)) for i in range(3)],
        )
        entries, _ = episode_evidence(repo, paths=["app.py"])
        assert "still_true" in entries[0]
        assert "still_true" not in entries[1]
        assert "still_true" not in entries[2]


# -- the count ---------------------------------------------------------------


class TestEpisodeCounts:
    def test_a_target_with_episodes_gets_an_integer(self, repo):
        _write(
            repo,
            _fix("sha1", ("app.py",), birth_commit=_head(repo)),
            _fix("sha2", ("app.py",), birth_commit=_head(repo)),
        )
        assert episode_counts(repo, ["app.py"]) == {"app.py": 2}

    def test_a_target_with_none_is_absent_rather_than_zero(self, repo):
        _write(repo, _fix("sha1", ("app.py",), birth_commit=_head(repo)))
        assert episode_counts(repo, ["other.py"]) == {}

    def test_a_repo_without_a_store_counts_nothing_and_creates_nothing(self, tmp_path):
        root = tmp_path / "bare"
        (root / ".repowise").mkdir(parents=True)
        assert episode_counts(root, ["app.py"]) == {}
        assert not default_store_path(root).exists()

    def test_transcript_episodes_are_not_counted(self, repo):
        """The count is a shareable claim and must not depend on whose laptop it is."""
        _write(
            repo,
            Episode(
                tier=TIER_TRANSCRIPT,
                kind="session",
                subject="s",
                body="a session",
                evidence="transcript",
                nodes=("app.py",),
                birth_at=1000.0,
            ),
            tier=TIER_TRANSCRIPT,
        )
        assert episode_counts(repo, ["app.py"]) == {}

    def test_enrichment_stamps_only_the_cards_that_have_any(self, repo):
        _write(repo, _fix("sha1", ("app.py",), birth_commit=_head(repo)))
        cards = [{"target": "app.py"}, {"target": "other.py"}]
        enrich_episode_counts(cards, repo)
        assert cards[0]["episodes"] == 1
        assert "episodes" not in cards[1]

    def test_enrichment_survives_a_card_with_no_target(self, repo):
        _write(repo, _fix("sha1", ("app.py",), birth_commit=_head(repo)))
        cards = [{"error": "could not resolve"}, {"target": "app.py"}]
        enrich_episode_counts(cards, repo)
        assert cards[1]["episodes"] == 1


# -- what the reviews found --------------------------------------------------


class TestPathsWithPatternCharacters:
    """A framework's dynamic routes are ordinary paths and must behave like it.

    ``GLOB`` reads ``[id]`` as a character class and has no ``ESCAPE`` clause,
    so the first cut matched ``repos/i`` and ``repos/d`` and missed every real
    child. 170 rows in this repository's own store carry bracketed paths.
    """

    def test_a_bracketed_directory_finds_its_own_children(self, repo):
        _write(
            repo,
            _fix("s1", ("src/app/[repo]/page.tsx",), birth_commit=_head(repo)),
            _fix("s2", ("src/app/[repo]/layout.tsx",), birth_commit=_head(repo)),
        )
        assert episode_counts(repo, ["src/app/[repo]"]) == {"src/app/[repo]": 2}

    def test_a_bracketed_directory_does_not_match_single_characters(self, repo):
        """``[repo]`` as a character class matches ``r``, ``e``, ``p``, ``o``."""
        _write(
            repo,
            _fix("s3", ("src/app/r/unrelated.tsx",), birth_commit=_head(repo)),
            _fix("s4", ("src/app/e/other.tsx",), birth_commit=_head(repo)),
        )
        assert episode_counts(repo, ["src/app/[repo]"]) == {}

    def test_star_and_question_are_literal(self, repo):
        _write(repo, _fix("s1", ("docs/aXb/deep.md",), birth_commit=_head(repo)))
        assert episode_counts(repo, ["docs/a?b"]) == {}
        assert episode_counts(repo, ["docs/a*b"]) == {}


class TestTargetShapes:
    """What a card asks about is not always a plain repo-relative file."""

    def test_a_symbol_target_is_counted_as_its_file(self, repo):
        """Splitting on ``/`` leaves ``service.py::Name``, which matches nothing.

        The count then fell through to the ancestor directory, so a symbol card
        read as quieter than its own file — the opposite of the truth.
        """
        _write(
            repo,
            _fix("s1", ("src/auth/service.py",), birth_commit=_head(repo)),
            _fix("s2", ("src/auth/service.py",), birth_commit=_head(repo)),
            _fix("s3", ("src/auth",), birth_commit=_head(repo)),
        )
        cards = [
            {"target": "src/auth/service.py"},
            {"target": "src/auth/service.py::AuthService"},
        ]
        enrich_episode_counts(cards, repo)
        assert cards[0]["episodes"] == cards[1]["episodes"] == 3

    def test_an_unresolved_target_is_not_stamped(self, repo):
        """A directory-bound episode matches any path beneath it, including a typo."""
        _write(repo, _fix("s1", ("src/auth",), birth_commit=_head(repo)))
        cards = [{"target": "src/auth/nope.py::Missing", "error": "Target not found"}]
        enrich_episode_counts(cards, repo)
        assert "episodes" not in cards[0]

    def test_a_renamed_file_keeps_its_history(self, repo):
        """Episodes are filed under the path the commit touched.

        Without the former name a file loses its whole history the moment it
        moves, which is exactly when an agent most wants it.
        """
        _write(repo, _fix("s1", ("old/name.py",), birth_commit=_head(repo)))
        cards = [{"target": "new/name.py", "original_path": "old/name.py"}]
        enrich_episode_counts(cards, repo)
        assert cards[0]["episodes"] == 1

    def test_a_rename_does_not_double_count_a_commit_touching_both_sides(self, repo):
        _write(repo, _fix("s1", ("new/name.py", "old/name.py"), birth_commit=_head(repo)))
        cards = [{"target": "new/name.py", "original_path": "old/name.py"}]
        enrich_episode_counts(cards, repo)
        assert cards[0]["episodes"] == 1


class TestScopeIsBounded:
    def test_a_sweep_commits_scope_is_capped_and_says_so(self, repo):
        _write(
            repo,
            _fix("s1", tuple(f"pkg/f{i}.py" for i in range(40)), birth_commit=_head(repo)),
        )
        entries, _ = episode_evidence(repo, paths=["pkg/f0.py"])
        scope = entries[0]["scope"]
        assert len(scope) == _MAX_SCOPE_NODES + 1
        assert scope[-1] == "… and 28 more"

    def test_an_empty_scope_reads_as_the_whole_checkout(self, repo):
        with EpisodeStore(default_store_path(repo)) as store:
            store.replace_kinds(
                tier=TIER_STRUCTURAL,
                kinds=["formatter_drift"],
                episodes=[
                    Episode(
                        tier=TIER_STRUCTURAL,
                        kind="formatter_drift",
                        subject="ruff format",
                        body="not formatter-clean",
                        evidence="ruff",
                        nodes=(),
                    )
                ],
            )
        entries, _ = episode_evidence(repo, query="formatter clean")
        assert entries[0]["scope"] == "the checkout as a whole"


class TestOverflowBanking:
    """The omission store is a sqlite3 connection bound to its creating thread."""

    def test_reading_banks_nothing_so_the_caller_owns_the_thread(self, repo):
        """``episode_evidence`` runs in a worker thread; ``attach`` does not.

        A collector opened there and finalised on the event loop raises inside
        ``_put``, which swallows it — losing every banked block silently,
        including the governing decisions the budget pass drops later.
        """
        _write(repo, _fix("s1", ("app.py",), birth_commit=_head(repo), body="x" * 5000))
        entries, pending = episode_evidence(repo, paths=["app.py"])
        assert len(pending) == 1
        entry, label, body = pending[0]
        assert entry is entries[0]
        assert label == "episode:code_fix"
        assert body == "x" * 5000
        # Nothing was written, so nothing is advertised yet.
        assert "repowise#" not in entries[0]["recorded"]

    def test_banking_nothing_makes_no_collector(self, repo):
        assert bank_overflow([], tool="get_why", repo_root=repo) is None


# -- end to end --------------------------------------------------------------


@pytest.fixture
def mcp_repo(setup_mcp, repo, monkeypatch):
    """Point the MCP server's globals at a checkout that has a real store.

    The shared fixture uses a path with no store at all, so every existing
    test short-circuits at the file-existence guard and the field could not
    reach a response no matter what it did.
    """
    from repowise.server.mcp_server import _state

    monkeypatch.setattr(_state, "_repo_path", str(repo))
    return repo


@pytest.mark.asyncio
async def test_get_context_serves_the_count_on_the_target_card(mcp_repo):
    from repowise.server.mcp_server import get_context

    _write(
        mcp_repo,
        _fix("s1", ("src/auth/service.py",), birth_commit=_head(mcp_repo)),
        _fix("s2", ("src/auth/service.py",), birth_commit=_head(mcp_repo)),
    )
    result = await get_context(["src/auth/service.py"])
    assert result["targets"]["src/auth/service.py"]["episodes"] == 2


@pytest.mark.asyncio
async def test_get_context_omits_the_count_where_there_is_none(mcp_repo):
    from repowise.server.mcp_server import get_context

    _write(mcp_repo, _fix("s1", ("src/auth/service.py",), birth_commit=_head(mcp_repo)))
    result = await get_context(["src/utils/helpers.py"])
    assert "episodes" not in result["targets"]["src/utils/helpers.py"]


@pytest.mark.asyncio
async def test_get_risk_serves_the_count_on_the_target_card(mcp_repo):
    from repowise.server.mcp_server import get_risk

    _write(mcp_repo, _fix("s1", ("src/auth/service.py",), birth_commit=_head(mcp_repo)))
    result = await get_risk(["src/auth/service.py"])
    assert result["targets"]["src/auth/service.py"]["episodes"] == 1


@pytest.mark.asyncio
async def test_get_why_path_mode_serves_the_evidence_block(mcp_repo):
    from repowise.server.mcp_server import get_why

    _write(mcp_repo, _fix("s1", ("src/auth/service.py",), birth_commit=_head(mcp_repo)))
    result = await get_why("src/auth/service.py")
    assert result["mode"] == "path"
    assert [e["kind"] for e in result["episodes"]] == ["code_fix"]
    assert result["episodes"][0]["still_true"]


@pytest.mark.asyncio
async def test_a_transcript_episode_reaches_no_surface(mcp_repo):
    """The tier allowlist, asserted on the payload rather than left as a comment."""
    from repowise.server.mcp_server import get_context, get_why

    _write(
        mcp_repo,
        Episode(
            tier=TIER_TRANSCRIPT,
            kind="session",
            subject="s",
            body="a session that touched it",
            evidence="transcript",
            nodes=("src/auth/service.py",),
            birth_at=1000.0,
        ),
        tier=TIER_TRANSCRIPT,
    )
    ctx = await get_context(["src/auth/service.py"])
    assert "episodes" not in ctx["targets"]["src/auth/service.py"]
    why = await get_why("src/auth/service.py")
    assert "episodes" not in why


@pytest.mark.asyncio
async def test_a_repo_without_a_store_is_unchanged(setup_mcp):
    """The default fixture path has no store; every response must be as before."""
    from repowise.server.mcp_server import get_context

    result = await get_context(["src/auth/service.py"])
    assert "episodes" not in result["targets"]["src/auth/service.py"]


@pytest.mark.asyncio
async def test_a_capped_body_is_banked_from_the_thread_that_attaches(mcp_repo):
    """The read runs in a worker thread; the omission store must not.

    A collector opened on the worker and finalised on the event loop raises
    inside ``_put``, which swallows the error and silently drops every banked
    block. The marker arriving is the proof it did not.
    """
    from repowise.server.mcp_server import get_why

    _write(
        mcp_repo,
        _fix("s1", ("src/auth/service.py",), birth_commit=_head(mcp_repo), body="y" * 5000),
    )
    result = await get_why("src/auth/service.py")
    recorded = result["episodes"][0]["recorded"]
    assert recorded.startswith("y" * _MAX_EVIDENCE_BODY_CHARS)
    assert "repowise#" in recorded
    assert result["_meta"]["omitted"]["refs"]
