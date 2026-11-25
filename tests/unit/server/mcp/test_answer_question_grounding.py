"""get_answer reads what a question names: files as files, phrases as symbol
names, history questions as history, and a question about nothing in the
repository as not found.

``populated_db`` indexes ``src/auth/service.py`` (``AuthService``, ``login``),
``src/auth/middleware.py``, ``src/db/models.py`` and ``tests/test_service.py``.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from repowise.core.persistence.models import GitMetadata, WikiSymbol
from repowise.server.mcp_server._query_shape import _names_symbol, defined_identifiers
from repowise.server.mcp_server.tool_answer.concept_names import (
    _phrases,
    concept_defining_files,
    name_forms,
)
from repowise.server.mcp_server.tool_answer.confidence import off_topic_words
from repowise.server.mcp_server.tool_answer.fix_history import asks_fix_history
from repowise.server.mcp_server.tool_answer.symbols import (
    _anchor_symbol_hits,
    _extract_question_identifiers,
)

# --- A file name is a file --------------------------------------------------


def test_a_file_extension_never_names_a_symbol():
    names = {"py", "PY", "ts", "proxyExecute"}
    assert not _names_symbol("call_resolver.py", names)
    assert not _names_symbol("index.ts", names)
    assert defined_identifiers("why so many fixes in call_resolver.py", names) == []
    # A dotted symbol chain still resolves by its last part.
    assert _names_symbol("client.proxyExecute", names)


def test_a_file_name_yields_its_stem_not_its_extension():
    assert _extract_question_identifiers("why so many fixes in call_resolver.py") == {
        "call_resolver"
    }
    assert _extract_question_identifiers("how does src/UserService.java validate") == {
        "UserService"
    }
    # A dotted chain that is not a file still yields every part.
    assert _extract_question_identifiers("what does Config.load_all do") == {
        "Config.load_all",
        "Config",
        "load_all",
    }


class _Result:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return self

    def all(self):
        return self._rows


class _Session:
    def __init__(self, *answers):
        self._answers = list(answers)

    async def execute(self, *a, **k):
        return _Result(self._answers.pop(0) if self._answers else [])


@pytest.mark.asyncio
async def test_the_stem_of_a_named_file_is_not_an_absent_name(tmp_path):
    """No line writes a script's stem; the question names the file, not code."""
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "run_backfill.py").write_text("print('go')\n", encoding="utf-8")
    question = "why does run_backfill.py keep failing on empty input?"
    _, homonyms = await _anchor_symbol_hits(
        _Session([]),
        "r",
        _extract_question_identifiers(question),
        [{"target_path": "scripts/run_backfill.py", "score": 3.0}],
        repo_root=tmp_path,
        question=question,
    )
    assert homonyms["absent"] == []


# --- A phrase that spells a symbol name -------------------------------------


def test_phrases_join_adjacent_plain_words_longest_first():
    phrases = _phrases("How is bus factor calculated, and where?")
    assert phrases[0] == ("bus", "factor", "calculated")
    assert ("bus", "factor") in phrases
    # A stopword or punctuation breaks the run; a typed identifier is not prose.
    assert ("calculated", "where") not in phrases
    assert _phrases("where is load_config called") == []


def test_name_forms_cover_each_casing_convention():
    assert name_forms(("bus", "factor")) == {
        "bus_factor",
        "_bus_factor",
        "__bus_factor",
        "BUS_FACTOR",
        "busFactor",
        "BusFactor",
    }


def _sym(name, path, kind="function", sid=None):
    now = datetime(2026, 1, 1, tzinfo=UTC)
    return WikiSymbol(
        id=sid or f"{path}::{name}",
        repository_id="repo1",
        file_path=path,
        symbol_id=f"{path}::{name}",
        name=name,
        qualified_name=name,
        kind=kind,
        signature=f"def {name}()",
        start_line=1,
        end_line=5,
        docstring="",
        visibility="private",
        is_async=False,
        complexity_estimate=1,
        language="python",
        parent_name=None,
        created_at=now,
        updated_at=now,
    )


@pytest.mark.asyncio
async def test_a_phrase_finds_the_function_its_words_spell(setup_mcp, session):
    session.add_all(
        [
            _sym("_bus_factor", "src/git/history.py"),
            _sym("BusFactor", "src/types/git.ts", kind="interface"),
            _sym("_bus_factor", "tests/test_history.py"),
        ]
    )
    await session.flush()
    files = await concept_defining_files(session, "repo1", "How is bus factor calculated?")
    # The function that computes it leads the type that carries it; a test
    # helper of the same name is not where it is defined.
    assert files == ["src/git/history.py", "src/types/git.ts"]
    assert await concept_defining_files(session, "repo1", "How are login tokens refreshed?") == []


@pytest.mark.asyncio
async def test_get_answer_places_the_spelled_function_in_the_window(
    setup_mcp, session, monkeypatch
):
    """Seven unrelated pages rank first; the defining file joins the top five."""
    import repowise.server.mcp_server.tool_answer.answer as answer_mod
    from repowise.core.persistence.database import get_session
    from repowise.server.mcp_server._helpers import _get_repo, _resolve_repo_context

    session.add(_sym("_bus_factor", "src/git/history.py"))
    await session.flush()
    files = [f"pkg/f{i}.py" for i in range(7)]

    async def _fake_retrieve(question, ctx):
        return [{"page_id": f"file_page:{p}", "score": 10.0 - i} for i, p in enumerate(files)]

    async def _fake_hydrate(hits, ctx, *, scope=None):
        for h in hits:
            h["target_path"] = h["page_id"].removeprefix("file_page:")
            h.update(title=h["target_path"], summary="", snippet="", page_type="file_page")
        return hits

    monkeypatch.setattr(answer_mod, "_hybrid_retrieve", _fake_retrieve)
    monkeypatch.setattr(answer_mod, "_hydrate_hits", _fake_hydrate)
    ctx = await _resolve_repo_context(None)
    async with get_session(ctx.session_factory) as s:
        repo_id = (await _get_repo(s)).id
    got = await answer_mod._run_retrieval_pipeline(
        "How is bus factor calculated?", ctx, scope=None, exclude_spec=None, repo_id=repo_id
    )
    paths = [h.get("target_path") for h in got.hits]
    assert paths[3] == "src/git/history.py"
    # Placed below the top three, level with the hit above: the lead is unchanged.
    assert paths[:3] == files[:3]
    assert got.hits[3]["score"] == got.hits[2]["score"]


# --- A history question gets the file's fix record --------------------------


@pytest.mark.parametrize(
    "question, asks",
    [
        ("Why are there so many fixes in call_resolver.py?", True),
        ("How often does service.py break?", True),
        ("Why does service.py keep changing?", True),
        ("Why does service.py change the token format?", False),
        ("How does service.py log in a user?", False),
    ],
)
def test_history_questions_are_told_apart(question, asks):
    assert asks_fix_history(question) is asks


async def _seed_fixes(session) -> None:
    row = (
        await session.execute(
            select(GitMetadata).where(GitMetadata.file_path == "src/auth/service.py")
        )
    ).scalar_one()
    row.prior_defect_count = 7
    row.bug_magnet = True
    row.last_fix_at = row.last_commit_at - timedelta(days=2)
    row.fix_symbol_counts_json = '{"src/auth/service.py::login": 4, "src/auth/service.py::AuthService": 2}'
    await session.flush()


@pytest.mark.asyncio
async def test_a_history_question_serves_the_named_files_fix_record(setup_mcp, session):
    from repowise.server.mcp_server import get_answer

    await _seed_fixes(session)
    result = await get_answer("Why are there so many fixes in service.py?")
    [history] = result["fix_history"]
    assert history["path"] == "src/auth/service.py"
    assert history["fix_count"] == 7
    assert history["bug_magnet"] is True
    assert history["last_fix_days_ago"] == 2
    assert history["top_symbols"] == {"login": 4, "AuthService": 2}


@pytest.mark.asyncio
async def test_other_questions_carry_no_fix_record(setup_mcp, session):
    from repowise.server.mcp_server import get_answer

    await _seed_fixes(session)
    result = await get_answer("How does service.py log in a user?")
    assert "fix_history" not in result


# --- A question about nothing in the repository -----------------------------


def _hit(path: str, text: str) -> dict:
    return {"target_path": path, "title": path, "summary": text, "score": 3.0}


_STRAY_HITS = [
    _hit("ui/tech-names.ts", "A known name keeps its capital letter."),
    _hit("core/public_api.py", "A module variable with no capital in its name is state."),
]


def test_an_answer_from_outside_the_evidence_is_off_topic():
    assert off_topic_words(
        "What is the capital of France?", "The capital of France is **Paris**.", _STRAY_HITS
    ) == ["france"]


def test_an_answer_that_uses_its_evidence_is_not_off_topic():
    # The question asks a word no hit carries, but the answer reads the hits.
    answer = "A known name keeps its capital letter (ui/tech-names.ts); variables are state."
    assert off_topic_words("What is the capital of France?", answer, _STRAY_HITS) == []
    # Every word the question asks is retrieved: on topic, whatever the answer says.
    assert off_topic_words("Which name keeps its capital?", "Paris.", _STRAY_HITS) == []


def test_a_reply_that_only_admits_the_gap_is_off_topic_too():
    """Nothing retrieved holds it, and the reply says so: not found either way."""
    answer = "The provided context does not mention France."
    assert off_topic_words("What is the capital of France?", answer, _STRAY_HITS) == ["france"]


@pytest.mark.asyncio
async def test_get_answer_serves_not_found_for_an_off_topic_question(setup_mcp, monkeypatch):
    import repowise.server.mcp_server.tool_answer.answer as answer_mod
    from repowise.server.mcp_server import get_answer

    async def _fake_retrieve(question, ctx):
        return [
            {"page_id": "file_page:src/auth/service.py", "score": 8.0},
            {"page_id": "file_page:src/db/models.py", "score": 2.0},
        ]

    async def _fake_hydrate(hits, ctx, *, scope=None):
        for h in hits:
            h["target_path"] = h["page_id"].removeprefix("file_page:")
            h.update(
                title=h["target_path"],
                summary="Keeps the capital letter of a user name.",
                snippet="",
                page_type="file_page",
            )
        return hits

    class _Provider:
        provider_name = "mock"
        model_name = "mock-1"

        async def generate(self, **kwargs):
            return SimpleNamespace(content="The capital of France is Paris.")

    monkeypatch.setattr(answer_mod, "_hybrid_retrieve", _fake_retrieve)
    monkeypatch.setattr(answer_mod, "_hydrate_hits", _fake_hydrate)
    monkeypatch.setattr(answer_mod, "_resolve_provider_for_answer", lambda _p: _Provider())

    result = await get_answer("What is the capital of France?")
    assert result["confidence"] == "low"
    assert result["answer"].startswith("Not found in this repository")
    assert "Paris" not in result["answer"]
    assert not result.get("citations")
    assert not result.get("fallback_targets")
    assert not result.get("candidate_files")
    assert "france" in result["answer"]
