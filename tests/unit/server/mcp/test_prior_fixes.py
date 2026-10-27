"""get_change_risk's prior_fixes block: past fixes on the lines a change touches.

The block is aggregate by construction. It reports how many fixes each changed
file has carried and how many changed lines land inside a past fix's replaced
ranges, and it never names the commit that introduced a bug — file-level SZZ ran
at 74.5% precision, enough to count and not enough to accuse.

It is also silent unless there is something to say, so a repo indexed before the
fix-event table existed sees no new block at all.
"""

from __future__ import annotations

import json
import subprocess
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from repowise.core.analysis.change_risk.service import history_ref
from repowise.core.analysis.prior_fix_impact import (
    FixRecord,
    parse_old_ranges,
    summarize_prior_fixes,
)
from repowise.core.persistence.database import init_db
from repowise.core.persistence.models import FixEvent, Repository, _new_uuid
from repowise.server.mcp_server.tool_change_risk import _prior_fixes_block


def _overlap(changed: set[int], old_ranges_json: str) -> int:
    """Changed lines landing inside a stored fix's replaced ranges.

    Both halves now live in core -- the tolerant parse and the count -- so this
    asserts them together the way the block consumes them.
    """
    record = FixRecord(fix_sha="a", file_path="f", old_ranges=parse_old_ranges(old_ranges_json))
    return summarize_prior_fixes([record], {"f": changed}).files[0].overlapping_lines


def test_overlap_counts_only_lines_inside_a_replaced_range():
    changed = {5, 6, 7, 40}
    assert _overlap(changed, json.dumps([[4, 7]])) == 3
    assert _overlap(changed, json.dumps([[4, 7], [39, 41]])) == 4
    assert _overlap(changed, json.dumps([[100, 120]])) == 0


def test_overlap_tolerates_missing_and_malformed_ranges():
    """A pure insertion stores an empty range list; that is 0, not a crash."""
    assert _overlap({1}, "[]") == 0
    assert _overlap({1}, "") == 0
    assert _overlap({1}, "not json") == 0
    assert _overlap({1}, json.dumps([[1]])) == 0
    assert _overlap({1}, json.dumps({"a": 1})) == 0


async def test_block_is_silent_without_an_index():
    ctx = SimpleNamespace(path="/repo")
    assert await _prior_fixes_block(ctx, {"a.py": {1}}) is None


async def test_block_is_silent_with_no_changed_lines():
    ctx = SimpleNamespace(path="/repo", session_factory=object())
    assert await _prior_fixes_block(ctx, {}) is None


_REPO_ID = "repo1"


async def _ctx_with_events(
    tmp_path,
    events: list[tuple[str, list, int]],
    *,
    shape_kind: str = "code_fix",
    shas: list[str] | None = None,
) -> SimpleNamespace:
    """A context over a real wiki.db seeded with ``(path, old_ranges, age_days)``.

    *shas* names each event's fix commit; synthetic shas otherwise.
    """
    tmp_path.mkdir(parents=True, exist_ok=True)
    db_path = tmp_path / "wiki.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path.as_posix()}")
    await init_db(engine)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        session.add(Repository(id=_REPO_ID, name="repo", local_path=str(tmp_path)))
        for i, (path, old_ranges, age_days) in enumerate(events):
            session.add(
                FixEvent(
                    id=_new_uuid(),
                    repository_id=_REPO_ID,
                    fix_sha=shas[i] if shas else f"{i:040x}",
                    file_path=path,
                    shape_kind=shape_kind,
                    old_ranges_json=json.dumps(old_ranges),
                    committed_at=datetime.now(UTC).replace(tzinfo=None) - timedelta(days=age_days),
                )
            )
        await session.commit()
    return SimpleNamespace(path=str(tmp_path), session_factory=factory)


async def test_block_aggregates_per_file_and_ranks_by_overlap(tmp_path):
    """The whole point of the block: which changed files have been patched here before."""
    ctx = await _ctx_with_events(
        tmp_path,
        [
            # app.py: two past fixes, one of which covers the lines being changed.
            ("src/app.py", [[10, 20]], 30),
            ("src/app.py", [[500, 510]], 5),
            # util.py: one past fix, nowhere near the changed lines.
            ("src/util.py", [[900, 910]], 100),
        ],
    )

    block = await _prior_fixes_block(ctx, {"src/app.py": {12, 13, 14}, "src/util.py": {1, 2}})

    assert block is not None
    assert block["total_fixes"] == 3
    assert block["files_with_fixes"] == 2
    # Ranked by overlap first, so the file whose changed lines sit on old fix
    # ground leads even though both files have fix history.
    assert [f["file_path"] for f in block["files"]] == ["src/app.py", "src/util.py"]

    app, util = block["files"]
    assert app["fix_count"] == 2
    assert app["overlapping_lines"] == 3
    # The MOST RECENT fix wins the age, not the last row the query happened to
    # return: "has this been a problem lately" is the question being answered.
    assert app["last_fix_days_ago"] == 5
    assert util["fix_count"] == 1
    assert util["overlapping_lines"] == 0

    # The overlap is hedged and the counts beside it are not, because only one
    # of the two is affected by lines having moved since the fix.
    assert block["line_overlap"] == "approximate"
    assert "approximate" in block["summary"]


async def test_block_ignores_files_the_change_does_not_touch(tmp_path):
    ctx = await _ctx_with_events(tmp_path, [("src/other.py", [[1, 5]], 10)])
    assert await _prior_fixes_block(ctx, {"src/app.py": {1}}) is None


async def test_non_code_fixes_are_not_counted(tmp_path):
    """A doc-only or test-only commit is not a bug fix in this file.

    The shape filter runs at index time for `prior_defect_count`; this block
    reads the raw events, so it has to apply the same rule or it would report a
    higher number than every other surface shows for the same file.
    """
    ctx = await _ctx_with_events(tmp_path, [("src/app.py", [[1, 5]], 10)], shape_kind="doc_only")
    assert await _prior_fixes_block(ctx, {"src/app.py": {1, 2, 3}}) is None


def _git(repo, *args: str) -> str:
    return subprocess.run(
        ["git", "-c", "user.name=Dev", "-c", "user.email=d@e.com", *args],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _fix(repo, content: str, message: str) -> str:
    (repo / "app.py").write_text(content, encoding="utf-8")
    _git(repo, "add", "app.py")
    _git(repo, "commit", "-q", "-m", message)
    return _git(repo, "rev-parse", "HEAD")


async def _reviewed_repo(tmp_path):
    """seed, an earlier fix, a feature, then the fix under review at HEAD."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _fix(repo, "v = 0\n", "feat: seed")
    earlier = _fix(repo, "v = 1\n", "fix: earlier crash")
    feature = _fix(repo, "v = 2\n", "feat: more")
    reviewed = _fix(repo, "v = 3\n", "fix: reviewed crash")
    ctx = await _ctx_with_events(
        repo, [("app.py", [[1, 1]], 30), ("app.py", [[1, 1]], 1)], shas=[earlier, reviewed]
    )
    return repo, ctx, earlier, feature, reviewed


async def test_a_reviewed_commit_is_not_its_own_fix_history(tmp_path):
    """The index holds the reviewed fix; reading it back as past ground is circular."""
    repo, ctx, *_, reviewed = await _reviewed_repo(tmp_path)

    block = await _prior_fixes_block(
        ctx, {"app.py": {1}}, history_ref=history_ref(str(repo), reviewed)
    )

    assert block["total_fixes"] == 1
    assert block["files"][0]["last_fix_days_ago"] == 30


async def test_a_range_reads_fixes_before_its_merge_base(tmp_path):
    repo, ctx, earlier, feature, _ = await _reviewed_repo(tmp_path)

    in_range = await _prior_fixes_block(
        ctx, {"app.py": {1}}, history_ref=history_ref(str(repo), f"{feature}..HEAD")
    )
    before_earlier = await _prior_fixes_block(
        ctx, {"app.py": {1}}, history_ref=history_ref(str(repo), f"{earlier}^..HEAD")
    )

    assert in_range["total_fixes"] == 1
    assert before_earlier is None


async def test_uncommitted_work_keeps_heads_fix(tmp_path):
    repo, ctx, *_ = await _reviewed_repo(tmp_path)

    block = await _prior_fixes_block(
        ctx, {"app.py": {1}}, history_ref=history_ref(str(repo), None, working_tree=True)
    )

    assert block["total_fixes"] == 2
