"""Pre-merge guard against silent retrieval regressions.

Indexes ``tests/fixtures/sample_repo`` for real (deterministic generation, the
SQL tables, full-text search and a keyless vector store, no API key), then asks
``search_codebase`` and ``get_answer`` every question in
``tests/fixtures/mcp/retrieval_guard_corpus.json`` through the real tool
middleware and scores the files each response serves against hand-read gold.

Two shipped regressions motivate it: one cut the files ``get_answer`` served
from about 19 to about 3, and one sent plain English words that equal exported
method names to symbol search. Neither failed a test, because no test measured
what a response actually serves.

Metrics, per arm, each a mean over questions:

- ``cov@1`` / ``cov@5``: share of gold in the first k served files, divided by
  ``min(k, len(gold))`` so a perfect ranking scores 1.0 at every depth.
- ``cov_all``: share of gold anywhere in the response.
- ``precision``: gold served / files served (0 when nothing is served).
- ``files`` and ``tokens``: medians of files served and response tokens.

Coverage is never read without the precision beside it: serving every file in
the repository would max out coverage and is not an improvement.

Fails when any coverage depth or precision drops by more than its tolerance
against ``tests/fixtures/mcp/retrieval_guard_baseline.json``, or median tokens
grow by more than ``TOKEN_TOLERANCE``. Improvements pass with a note asking for
a baseline refresh. To refresh after an intentional ranking change::

    REPOWISE_UPDATE_RETRIEVAL_BASELINE=1 uv run pytest tests/unit/server/mcp/test_retrieval_guard.py -s

and commit the baseline with the change, quoting the before/after table.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import statistics
from pathlib import Path

import pytest

FIXTURES = Path(__file__).resolve().parents[3] / "fixtures"
SAMPLE_REPO = FIXTURES / "sample_repo"
CORPUS_PATH = FIXTURES / "mcp" / "retrieval_guard_corpus.json"
BASELINE_PATH = FIXTURES / "mcp" / "retrieval_guard_baseline.json"
UPDATE_ENV = "REPOWISE_UPDATE_RETRIEVAL_BASELINE"

# Absolute drops on a 0-1 scale. 0.03 lets one single-gold question move
# (1/48 = 0.021) and fails on two.
COVERAGE_TOLERANCE = 0.03
PRECISION_TOLERANCE = 0.03
# Relative growth of the median response.
TOKEN_TOLERANCE = 0.15

COVERAGE_KEYS = ("cov@1", "cov@5", "cov_all")
ARMS = {
    "search": ("search_codebase", {}),
    "search_limit10": ("search_codebase", {"limit": 10}),
    "answer": ("get_answer", {}),
}

# Top-level keys read first, in this order, so served order follows what an
# agent reads first. Every other key is still walked afterwards.
_PRIORITY_KEYS = (
    "citations",
    "results",
    "best_guesses",
    "candidate_files",
    "candidates",
    "fallback_targets",
    "retrieval",
)
_LINE_SUFFIX = re.compile(r":\d+(?:-\d+)?$")


def _load_corpus() -> list[dict]:
    return json.loads(CORPUS_PATH.read_text(encoding="utf-8"))["questions"]


def _normalise(raw: object, known: set[str]) -> str | None:
    if not isinstance(raw, str):
        return None
    path = _LINE_SUFFIX.sub("", raw.split("::", 1)[0].strip().replace("\\", "/"))
    return path if path in known else None


def served_files(response: dict, known: set[str]) -> list[str]:
    """Distinct repository files a response serves, in reading order.

    Any string that is exactly an indexed file (after dropping a ``::Symbol``
    or ``:line`` suffix) counts, wherever it sits, so a new path-bearing field
    is measured without touching this file. Page hits resolve through the
    server's own ``hit_file_path``. ``_meta`` is skipped: ``scope_hint`` names
    areas the answer did *not* touch.
    """
    from repowise.server.mcp_server._page_paths import hit_file_path

    out: list[str] = []

    def add(raw: object) -> None:
        path = _normalise(raw, known)
        if path and path not in out:
            out.append(path)

    def walk(node: object) -> None:
        if isinstance(node, str):
            add(node)
        elif isinstance(node, list):
            for item in node:
                walk(item)
        elif isinstance(node, dict):
            if node.get("page_type"):
                add(hit_file_path(node))
                node = {k: v for k, v in node.items() if k != "target_path"}
            for value in node.values():
                walk(value)

    keys = [k for k in _PRIORITY_KEYS if k in response]
    keys += [k for k in response if k not in keys and k != "_meta"]
    for key in keys:
        walk(response[key])
    return out


def _without_timing(node: object) -> object:
    if isinstance(node, dict):
        return {k: _without_timing(v) for k, v in node.items() if k != "timing_ms"}
    if isinstance(node, list):
        return [_without_timing(v) for v in node]
    return node


def score(rows: list[dict]) -> dict:
    """Aggregate per-question rows of ``{gold, served, tokens}``."""

    def cov(gold: list[str], served: list[str], k: int | None) -> float:
        window = served if k is None else served[:k]
        denom = len(gold) if k is None else min(k, len(gold))
        return len(set(gold) & set(window)) / denom

    def mean(values: list[float]) -> float:
        return round(sum(values) / len(values), 4)

    return {
        "cov@1": mean([cov(r["gold"], r["served"], 1) for r in rows]),
        "cov@5": mean([cov(r["gold"], r["served"], 5) for r in rows]),
        "cov_all": mean([cov(r["gold"], r["served"], None) for r in rows]),
        "precision": mean(
            [len(set(r["gold"]) & set(r["served"])) / len(r["served"]) if r["served"] else 0.0
             for r in rows]
        ),
        "files": statistics.median(len(r["served"]) for r in rows),
        "tokens": statistics.median(r["tokens"] for r in rows),
    }


def _table(metrics: dict, baseline: dict | None) -> str:
    cols = ("cov@1", "cov@5", "cov_all", "precision", "files", "tokens")
    lines = [f"{'arm':<15}" + "".join(f"{c:>17}" for c in cols)]
    for arm, values in metrics.items():
        cells = []
        for c in cols:
            now = values[c]
            was = (baseline or {}).get(arm, {}).get(c)
            cells.append(f"{now:g}" if was is None or was == now else f"{was:g} -> {now:g}")
        lines.append(f"{arm:<15}" + "".join(f"{cell:>17}" for cell in cells))
    return "\n".join(lines)


def compare(metrics: dict, baseline: dict) -> tuple[list[str], list[str]]:
    """Return ``(regressions, improvements)`` against *baseline*."""
    regressions: list[str] = []
    improvements: list[str] = []
    for arm, now in metrics.items():
        was = baseline.get(arm)
        if was is None:
            regressions.append(f"{arm}: no baseline row; refresh the baseline")
            continue
        for key, tol in [(k, COVERAGE_TOLERANCE) for k in COVERAGE_KEYS] + [
            ("precision", PRECISION_TOLERANCE)
        ]:
            if now[key] < was[key] - tol:
                regressions.append(f"{arm} {key}: {was[key]} -> {now[key]} (tolerance {tol})")
            elif now[key] > was[key]:
                improvements.append(f"{arm} {key}: {was[key]} -> {now[key]}")
        if now["tokens"] > was["tokens"] * (1 + TOKEN_TOLERANCE):
            regressions.append(
                f"{arm} median tokens: {was['tokens']} -> {now['tokens']} "
                f"(tolerance +{TOKEN_TOLERANCE:.0%})"
            )
    return regressions, improvements


async def _index_sample_repo(root: Path):
    """Index a copy of the fixture the way ``repowise init --index-only`` does.

    A copy, outside any git checkout, so git signals are absent rather than
    whatever the surrounding checkout's history happens to be.
    """
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
    from sqlalchemy.pool import StaticPool

    from repowise.core.persistence import init_db, upsert_repository
    from repowise.core.persistence.search import FullTextSearch
    from repowise.core.persistence.vector_store import InMemoryVectorStore
    from repowise.core.pipeline import persist_pipeline_result, run_pipeline
    from repowise.core.pipeline.modes import OrchestratorMode
    from repowise.core.providers.embedding.base import KeylessEmbedder

    repo = root / "sample_repo"
    shutil.copytree(SAMPLE_REPO, repo)
    embedder = KeylessEmbedder()
    vector_store = InMemoryVectorStore(embedder=embedder)
    result = await run_pipeline(
        repo,
        mode=OrchestratorMode.DETERMINISTIC,
        generate_docs=True,
        embedder=embedder,
        vector_store=vector_store,
        concurrency=3,
    )
    pages = list(result.generated_pages or [])
    known = {fi.path for fi in result.file_infos}

    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    await init_db(engine)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with factory() as session:
        row = await upsert_repository(session, name="sample_repo", local_path=str(repo))
        await persist_pipeline_result(result, session, row.id)
        await session.commit()
    fts = FullTextSearch(engine)
    await fts.ensure_index()
    await fts.index_pages(pages)
    return engine, factory, fts, vector_store, repo, known


def test_corpus_gold_names_real_fixture_files() -> None:
    questions = _load_corpus()
    assert 30 <= len(questions) <= 50
    assert len({q["id"] for q in questions}) == len(questions)
    assert {q["shape"] for q in questions} == {
        "issue",
        "identifier",
        "path",
        "concept",
        "multi_file",
    }
    for q in questions:
        assert q["query"].strip() and q["basis"].strip(), q["id"]
        for gold in q["gold"]:
            assert (SAMPLE_REPO / gold).is_file(), f"{q['id']}: {gold} is not a fixture file"


def test_served_files_reads_every_path_bearing_field() -> None:
    known = {"a.py", "b.py", "c.py", "d.py", "e.py", "f.py"}
    response = {
        "answer": "see a.py",
        "best_guesses": [{"file": "b.py", "excerpt": "c.py defines Thing"}],
        "citations": ["a.py:10-20"],
        "results": [
            {"page_type": "module_page", "target_path": "f.py"},
            {"page_type": "symbol_spotlight", "target_path": "c.py::Thing"},
        ],
        "symbol_bodies": [{"file_path": "d.py"}],
        "candidate_files": ["e.py"],
        "_meta": {"scope_hint": "f.py", "targets": ["f.py"]},
    }
    assert served_files(response, known) == ["a.py", "c.py", "b.py", "e.py", "d.py"]


async def test_retrieval_does_not_regress(tmp_path, monkeypatch) -> None:
    import asyncio
    import time

    from repowise.core.persistence.vector_store import InMemoryVectorStore
    from repowise.core.providers.embedding.base import KeylessEmbedder
    from repowise.server.mcp_server import _state, tool_middleware
    from repowise.server.mcp_server._budget.budgeter import estimate_response_tokens
    from repowise.server.mcp_server.tool_answer import answer as answer_mod

    started = time.perf_counter()
    engine, factory, fts, vector_store, repo, known = await _index_sample_repo(tmp_path)
    indexed = time.perf_counter()

    # No provider may resolve, whatever keys the environment holds: the guard
    # measures retrieval, and CI has no keys anyway.
    monkeypatch.setattr(answer_mod, "_resolve_provider_for_answer", lambda _p: None)
    ready = asyncio.Event()
    ready.set()
    for name, value in {
        "_session_factory": factory,
        "_fts": fts,
        "_vector_store": vector_store,
        "_decision_store": InMemoryVectorStore(embedder=KeylessEmbedder()),
        "_repo_path": str(repo),
        # Unset, every concept search waits 30 s for a readiness signal.
        "_vector_store_ready": ready,
    }.items():
        monkeypatch.setattr(_state, name, value, raising=False)

    import repowise.server.mcp_server as mcp_mod

    tools = {name: tool_middleware(getattr(mcp_mod, name)) for name in ("search_codebase", "get_answer")}
    questions = _load_corpus()
    rows: dict[str, list[dict]] = {arm: [] for arm in ARMS}
    try:
        for q in questions:
            for arm, (tool, kwargs) in ARMS.items():
                first = "query" if tool == "search_codebase" else "question"
                response = await tools[tool](**{first: q["query"]}, **kwargs)
                rows[arm].append(
                    {
                        "id": q["id"],
                        "gold": q["gold"],
                        "served": served_files(response, known),
                        "tokens": estimate_response_tokens(_without_timing(response)),
                    }
                )
    finally:
        await vector_store.close()
        await engine.dispose()

    metrics = {arm: score(arm_rows) for arm, arm_rows in rows.items()}
    per_question = {
        arm: {r["id"]: r["served"][:5] for r in arm_rows} for arm, arm_rows in rows.items()
    }
    baseline = (
        json.loads(BASELINE_PATH.read_text(encoding="utf-8")) if BASELINE_PATH.exists() else None
    )
    table = _table(metrics, (baseline or {}).get("metrics"))
    report = (
        f"retrieval guard: {len(questions)} questions, index {indexed - started:.1f}s, "
        f"queries {time.perf_counter() - indexed:.1f}s\n{table}"
    )
    print("\n" + report)
    if summary := os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(summary, "a", encoding="utf-8") as fh:
            fh.write(f"### Retrieval guard\n\n```\n{report}\n```\n")

    if os.environ.get(UPDATE_ENV):
        BASELINE_PATH.write_text(
            json.dumps(
                {"metrics": metrics, "top5_served": per_question}, indent=2, sort_keys=True
            )
            + "\n",
            encoding="utf-8",
        )
        return

    assert baseline is not None, f"no baseline; run with {UPDATE_ENV}=1 to create it"
    regressions, improvements = compare(metrics, baseline["metrics"])
    if regressions:
        moved = [
            f"  {arm} {qid}: {was} -> {per_question[arm].get(qid)}"
            for arm, by_id in baseline.get("top5_served", {}).items()
            for qid, was in by_id.items()
            if arm in per_question and per_question[arm].get(qid) != was
        ]
        pytest.fail(
            "retrieval regressed beyond tolerance:\n  "
            + "\n  ".join(regressions)
            + f"\n\n{table}\n\nquestions whose top 5 moved:\n"
            + "\n".join(moved[:40])
            + f"\n\nIf intended, refresh with {UPDATE_ENV}=1 (see module docstring)."
        )
    if improvements:
        print(
            "retrieval improved; refresh the baseline so the gain is locked in:\n  "
            + "\n  ".join(improvements)
        )
