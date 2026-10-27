"""Git history windows are measured from the indexed commit, not the wall clock.

The same tree indexed on two different days must produce the same git metadata
and the same blame-based health findings. The clock is stubbed so the test
proves the product never reads it, and a wall-clock control proves the stub
actually bites.
"""

from __future__ import annotations

import json
import time
from datetime import UTC, datetime

import pytest

from repowise.core.analysis.health.biomarkers.base import FileContext
from repowise.core.analysis.health.biomarkers.code_age_volatility import (
    CodeAgeVolatilityDetector,
)
from repowise.core.analysis.health.complexity import FunctionComplexity
from repowise.core.analysis.health.function_blame_rollup import build_function_blame_rows
from repowise.core.ingestion.git_indexer import GitIndexer, file_history, prior_defects
from repowise.core.ingestion.git_indexer.file_history import DECAY_REFRESH_KEYS
from repowise.core.ingestion.git_indexer.function_blame import BlameIndex
from repowise.core.ingestion.git_indexer.tiers import GitIndexTier

# Two "todays": one just after the last commit, one years later.
_NOW_A = datetime(2024, 3, 20, tzinfo=UTC).timestamp()
_NOW_B = datetime(2026, 9, 1, tzinfo=UTC).timestamp()
_HEAD_DATE = "2024-03-15T00:00:00"


def _stub_clock(monkeypatch: pytest.MonkeyPatch, now: float) -> None:
    class _Frozen(datetime):
        @classmethod
        def now(cls, tz=None):  # type: ignore[override]
            return datetime.fromtimestamp(now, tz=tz)

    monkeypatch.setattr(time, "time", lambda: now)
    monkeypatch.setattr(file_history, "datetime", _Frozen)
    monkeypatch.setattr(prior_defects, "datetime", _Frozen)


def _build_repo(root) -> None:
    import git as gitpython

    repo = gitpython.Repo.init(root)
    with repo.config_writer() as cw:
        cw.set_value("user", "name", "Alice")
        cw.set_value("user", "email", "alice@example.com")

    def commit(files: dict[str, str], msg: str, date: str) -> None:
        for name, content in files.items():
            (root / name).write_text(content)
        repo.index.add(list(files))
        repo.index.commit(msg, author_date=date, commit_date=date)

    body = "".join(f"def f{i}(x):\n    return x + {i}\n\n" for i in range(6))
    commit({"a.py": body, "b.py": "y = 0\n"}, "feat: add modules", "2021-01-01T00:00:00")
    for i in range(5):
        text = body.replace(f"x + {i}", f"x * {i + 10}")
        date = f"2024-0{1 + i % 3}-0{i + 1}T00:00:00"
        commit({"a.py": text, "b.py": f"y = {i}\n"}, f"fix: bug {i} in f{i}", date)
    commit({"b.py": "y = 99\n"}, "feat: tune b", _HEAD_DATE)
    repo.close()


async def _index(root, monkeypatch, now: float) -> dict[str, dict]:
    _stub_clock(monkeypatch, now)
    _summary, rows = await GitIndexer(root, tier=GitIndexTier.FULL).index_repo("r")
    return {row["file_path"]: row for row in rows}


def _comparable(row: dict) -> dict:
    return {k: v for k, v in row.items() if k != "blame_index"}


def _health(meta: dict) -> tuple[list, list]:
    idx = meta["a.py"]["blame_index"]
    fns = tuple(
        FunctionComplexity(
            name=f"f{i}",
            start_line=3 * i + 1,
            end_line=3 * i + 2,
            ccn=1,
            max_nesting=0,
            cognitive=0,
            nloc=2,
        )
        for i in range(6)
    )
    ctx = FileContext(
        file_path="a.py",
        language="python",
        nloc=18,
        has_test_file=False,
        module=None,
        all_functions=fns,
        blame_index=idx,
    )
    findings = [(f.function_name, f.details) for f in CodeAgeVolatilityDetector().detect(ctx)]

    class _Pf:
        class file_info:  # noqa: N801
            path = "a.py"

    walked = [(_Pf, type("Fcx", (), {"functions": list(fns)})())]
    rows = build_function_blame_rows(walked, meta)
    return findings, rows


async def test_same_commit_two_clocks_same_metadata_and_health(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("REPOWISE_GIT_WINDOW_ANCHOR", raising=False)
    _build_repo(tmp_path)

    first = await _index(tmp_path, monkeypatch, _NOW_A)
    health_a = _health(first)
    second = await _index(tmp_path, monkeypatch, _NOW_B)
    health_b = _health(second)

    assert {k: _comparable(v) for k, v in first.items()} == {
        k: _comparable(v) for k, v in second.items()
    }
    assert health_a == health_b
    # The windows are live: HEAD's trailing 90 days hold the 2024 commits.
    assert first["a.py"]["commit_count_90d"] == 5
    assert first["a.py"]["prior_defect_count"] > 0
    head_ts = int(datetime.fromisoformat(_HEAD_DATE).replace(tzinfo=UTC).timestamp())
    assert first["a.py"]["blame_index"].as_of_ts == head_ts
    assert json.loads(first["a.py"]["co_change_partners_json"])


async def test_wall_clock_opt_in_still_reads_the_clock(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("REPOWISE_GIT_WINDOW_ANCHOR", "now")
    _build_repo(tmp_path)

    first = await _index(tmp_path, monkeypatch, _NOW_A)
    second = await _index(tmp_path, monkeypatch, _NOW_B)

    assert first["a.py"]["commit_count_90d"] == 5
    assert second["a.py"]["commit_count_90d"] == 0
    assert first["a.py"]["prior_defect_count"] > second["a.py"]["prior_defect_count"]


async def test_update_uses_the_same_anchor_as_a_full_index(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("REPOWISE_GIT_WINDOW_ANCHOR", raising=False)
    _build_repo(tmp_path)
    full = await _index(tmp_path, monkeypatch, _NOW_A)

    _stub_clock(monkeypatch, _NOW_B)
    sink: dict[str, dict] = {}
    changed = await GitIndexer(tmp_path, tier=GitIndexTier.FULL).index_changed_files(
        ["b.py"], all_files={"a.py", "b.py"}, idle_decay_sink=sink
    )

    changed_b = {row["file_path"]: row for row in changed}["b.py"]
    for key in (*DECAY_REFRESH_KEYS, "age_days"):
        assert changed_b[key] == full["b.py"][key], key
    for key, value in sink["a.py"].items():
        assert value == full["a.py"][key], key


def test_code_age_volatility_measures_from_the_blame_anchor(monkeypatch) -> None:
    day = 86400
    anchor = int(_NOW_A)
    lines = {ln: (f"a{ln:039d}", anchor - 800 * day) for ln in range(1, 8)}
    lines.update({ln: (f"b{ln:039d}", anchor - 3 * day) for ln in range(8, 11)})
    fc = FunctionComplexity(
        name="legacy", start_line=1, end_line=10, ccn=1, max_nesting=0, cognitive=0, nloc=10
    )
    ctx = FileContext(
        file_path="a.py",
        language="python",
        nloc=10,
        has_test_file=False,
        module=None,
        all_functions=(fc,),
        blame_index=BlameIndex(lines=lines, as_of_ts=anchor),
    )

    monkeypatch.setattr(time, "time", lambda: _NOW_B)
    findings = CodeAgeVolatilityDetector().detect(ctx)

    assert len(findings) == 1
    assert findings[0].details == {"median_age_days": 800, "recent_mod_count": 3}
