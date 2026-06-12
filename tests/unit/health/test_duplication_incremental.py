"""Incremental duplication splice must reproduce the full recompute exactly.

Oracle pattern: every scenario seeds a repo, runs a full cached pass
(persisting the pair index), mutates the tree, then asserts the
incremental run (``changed_files=...``) equals a fresh full recompute
of the mutated tree — pairs as a multiset including token_count (the
merge stage accumulates it, so multiplicity drift would surface there),
plus duplication_pct and pairs_by_file.
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path
from types import SimpleNamespace

from repowise.core.analysis.health.duplication import detect_clones
from repowise.core.analysis.health.duplication.limits import DuplicationLimits
from repowise.core.analysis.health.duplication.pair_index import (
    _INDEX_FILENAME,
    load_pair_index,
)

WINDOW = 20
MIN_LINES = 4
# Deterministic limits: no wall-clock deadline in tests.
LIMITS = DuplicationLimits(time_budget_secs=0)


def _pf(tmp_path: Path, rel: str) -> SimpleNamespace:
    return SimpleNamespace(
        file_info=SimpleNamespace(path=rel, abs_path=str(tmp_path / rel), language="python"),
        symbols=[],
    )


_BODY = "\n".join(
    [
        "def doit(x, y, z):",
        "    if x:",
        "        a = x + y",
        "    else:",
        "        a = x - y",
        "    if z:",
        "        b = a * 2",
        "    else:",
        "        b = a - 1",
        "    return a + b + x + y + z",
        "",
    ]
)

_OTHER = "\n".join(
    [
        "def other(p, q):",
        "    total = 0",
        "    for i in range(p):",
        "        if i % 2:",
        "            total += i * q",
        "        else:",
        "            total -= i + q",
        "    return total",
        "",
    ]
)


def _write(tmp_path: Path, files: dict[str, str]) -> list[SimpleNamespace]:
    for rel, body in files.items():
        (tmp_path / rel).write_text(body)
    return [_pf(tmp_path, rel) for rel in sorted(files)]


def _parsed(tmp_path: Path) -> list[SimpleNamespace]:
    return [_pf(tmp_path, p.name) for p in sorted(tmp_path.glob("*.py"))]


def _key(report):
    return (
        sorted(
            Counter(
                (
                    p.file_a,
                    p.file_b,
                    p.a_start_line,
                    p.a_end_line,
                    p.b_start_line,
                    p.b_end_line,
                    p.token_count,
                    p.co_change_count,
                )
                for p in report.pairs
            ).items()
        ),
        report.duplication_pct,
        {f: len(ps) for f, ps in report.pairs_by_file.items()},
    )


def _full(parsed, limits=LIMITS):
    """Fresh full recompute, no cache — the oracle."""
    return detect_clones(parsed, window_tokens=WINDOW, min_lines=MIN_LINES, limits=limits)


def _incremental(parsed, cache_dir, changed, limits=LIMITS):
    return detect_clones(
        parsed,
        window_tokens=WINDOW,
        min_lines=MIN_LINES,
        limits=limits,
        cache_dir=cache_dir,
        changed_files=set(changed),
    )


def _seed(tmp_path: Path, files: dict[str, str], limits=LIMITS):
    """Initial full cached run; persists token cache + pair index."""
    parsed = _write(tmp_path, files)
    cache_dir = tmp_path / ".repowise"
    detect_clones(
        parsed, window_tokens=WINDOW, min_lines=MIN_LINES, limits=limits, cache_dir=cache_dir
    )
    assert (cache_dir / _INDEX_FILENAME).exists()
    return cache_dir


def test_modify_clone_member(tmp_path: Path):
    cache_dir = _seed(tmp_path, {"a.py": _BODY, "b.py": _BODY, "c.py": _OTHER})
    (tmp_path / "a.py").write_text(_OTHER.replace("other", "mutated"))

    parsed = _parsed(tmp_path)
    report = _incremental(parsed, cache_dir, {"a.py"})
    assert report.diagnostics.get("incremental") is True
    assert _key(report) == _key(_full(parsed))


def test_add_new_clone_file(tmp_path: Path):
    cache_dir = _seed(tmp_path, {"a.py": _BODY, "c.py": _OTHER})
    (tmp_path / "d.py").write_text(_BODY.replace("doit", "added"))

    parsed = _parsed(tmp_path)
    report = _incremental(parsed, cache_dir, {"d.py"})
    assert report.diagnostics.get("incremental") is True
    assert any({p.file_a, p.file_b} == {"a.py", "d.py"} for p in report.pairs)
    assert _key(report) == _key(_full(parsed))


def test_delete_clone_file(tmp_path: Path):
    cache_dir = _seed(tmp_path, {"a.py": _BODY, "b.py": _BODY, "c.py": _OTHER})
    (tmp_path / "b.py").unlink()

    parsed = _parsed(tmp_path)
    # Deletions arrive via the parsed set shrinking, not changed_files.
    report = _incremental(parsed, cache_dir, set())
    assert report.diagnostics.get("incremental") is True
    assert not report.pairs
    assert _key(report) == _key(_full(parsed))


def test_rename_clone_file(tmp_path: Path):
    cache_dir = _seed(tmp_path, {"a.py": _BODY, "b.py": _BODY})
    (tmp_path / "b.py").unlink()
    (tmp_path / "renamed.py").write_text(_BODY)

    parsed = _parsed(tmp_path)
    report = _incremental(parsed, cache_dir, {"renamed.py"})
    assert report.diagnostics.get("incremental") is True
    assert any({p.file_a, p.file_b} == {"a.py", "renamed.py"} for p in report.pairs)
    assert _key(report) == _key(_full(parsed))


def test_noop_change_listed_as_changed(tmp_path: Path):
    files = {"a.py": _BODY, "b.py": _BODY, "c.py": _OTHER}
    cache_dir = _seed(tmp_path, files)
    (tmp_path / "a.py").write_text(_BODY)  # rewrite identical content

    parsed = _parsed(tmp_path)
    report = _incremental(parsed, cache_dir, {"a.py"})
    assert report.diagnostics.get("incremental") is True
    assert _key(report) == _key(_full(parsed))


def test_intra_file_duplication_in_changed_file(tmp_path: Path):
    cache_dir = _seed(tmp_path, {"a.py": _BODY, "c.py": _OTHER})
    intra = _BODY + "\n" + _BODY.replace("doit", "again")
    (tmp_path / "a.py").write_text(intra)

    parsed = _parsed(tmp_path)
    report = _incremental(parsed, cache_dir, {"a.py"})
    assert report.diagnostics.get("incremental") is True
    assert any(p.is_intra_file for p in report.pairs)
    assert _key(report) == _key(_full(parsed))


def test_degenerate_bucket_shrinks_below_cap(tmp_path: Path):
    """Removing a member can revive a previously capped bucket's pairs."""
    lim = DuplicationLimits(time_budget_secs=0, max_bucket_windows=3)
    files = {f"{n}.py": _BODY for n in "abcd"}  # buckets of 4 > cap of 3
    cache_dir = _seed(tmp_path, files, limits=lim)
    baseline = load_pair_index(cache_dir, WINDOW, lim)
    assert baseline is not None and not baseline.pairs  # all degenerate

    (tmp_path / "d.py").unlink()  # buckets drop to 3 == cap -> pairs emerge
    parsed = _parsed(tmp_path)
    report = _incremental(parsed, cache_dir, set(), limits=lim)
    assert report.diagnostics.get("incremental") is True
    assert report.pairs
    assert _key(report) == _key(_full(parsed, limits=lim))


def test_degenerate_bucket_grows_past_cap(tmp_path: Path):
    """Adding a member can cap a bucket, removing unchanged-pair output."""
    lim = DuplicationLimits(time_budget_secs=0, max_bucket_windows=3)
    files = {f"{n}.py": _BODY for n in "abc"}  # buckets of 3 == cap -> pairs
    cache_dir = _seed(tmp_path, files, limits=lim)
    baseline = load_pair_index(cache_dir, WINDOW, lim)
    assert baseline is not None and baseline.pairs

    (tmp_path / "d.py").write_text(_BODY)  # buckets of 4 > cap
    parsed = _parsed(tmp_path)
    report = _incremental(parsed, cache_dir, {"d.py"}, limits=lim)
    assert report.diagnostics.get("incremental") is True
    assert not report.pairs
    assert _key(report) == _key(_full(parsed, limits=lim))


def test_chained_incremental_updates(tmp_path: Path):
    """The artifact rewritten by one splice must support the next."""
    cache_dir = _seed(tmp_path, {"a.py": _BODY, "b.py": _BODY, "c.py": _OTHER})

    (tmp_path / "d.py").write_text(_BODY.replace("doit", "fourth"))
    parsed = _parsed(tmp_path)
    first = _incremental(parsed, cache_dir, {"d.py"})
    assert first.diagnostics.get("incremental") is True
    assert _key(first) == _key(_full(parsed))

    (tmp_path / "b.py").write_text(_OTHER.replace("other", "swapped"))
    parsed = _parsed(tmp_path)
    second = _incremental(parsed, cache_dir, {"b.py"})
    assert second.diagnostics.get("incremental") is True
    assert _key(second) == _key(_full(parsed))


def test_gated_files_are_not_treated_as_new(tmp_path: Path, monkeypatch):
    """Files the seed run gated out (too small to window) must not count
    toward the changed-files guard on every later run."""
    from repowise.core.analysis.health.duplication import detector

    monkeypatch.setattr(detector, "_CHANGED_COUNT_FLOOR", 1)
    files = {"a.py": _BODY, "b.py": _BODY}
    files.update({f"tiny{i}.py": f"x = {i}\n" for i in range(6)})
    cache_dir = _seed(tmp_path, files)
    idx = load_pair_index(cache_dir, WINDOW, LIMITS)
    assert idx is not None and len(idx.nonsurvivors) == 6

    (tmp_path / "a.py").write_text(_BODY.replace("doit", "edited"))
    parsed = _parsed(tmp_path)
    # Only 1 real change; with the tiny files miscounted as new this
    # would exceed the floor of 1 and fall back.
    report = _incremental(parsed, cache_dir, {"a.py"})
    assert report.diagnostics.get("incremental") is True
    assert _key(report) == _key(_full(parsed))


def test_gated_file_growing_into_survivor(tmp_path: Path):
    """A previously gated file that changes into real content joins in."""
    cache_dir = _seed(tmp_path, {"a.py": _BODY, "tiny.py": "x = 1\n"})
    (tmp_path / "tiny.py").write_text(_BODY.replace("doit", "grown"))

    parsed = _parsed(tmp_path)
    report = _incremental(parsed, cache_dir, {"tiny.py"})
    assert report.diagnostics.get("incremental") is True
    assert any({p.file_a, p.file_b} == {"a.py", "tiny.py"} for p in report.pairs)
    assert _key(report) == _key(_full(parsed))


def test_too_many_changes_falls_back_to_full(tmp_path: Path, monkeypatch):
    from repowise.core.analysis.health.duplication import detector

    monkeypatch.setattr(detector, "_CHANGED_COUNT_FLOOR", 0)
    files = {f"f{i}.py": _BODY.replace("doit", f"fn{i}") for i in range(5)}
    cache_dir = _seed(tmp_path, files)
    for i in range(3):  # 3 of 5 changed > 20% threshold
        (tmp_path / f"f{i}.py").write_text(_OTHER.replace("other", f"fn{i}"))

    parsed = _parsed(tmp_path)
    report = _incremental(parsed, cache_dir, {"f0.py", "f1.py", "f2.py"})
    assert "incremental" not in report.diagnostics
    assert _key(report) == _key(_full(parsed))


def test_limits_change_invalidates_artifact(tmp_path: Path):
    cache_dir = _seed(tmp_path, {"a.py": _BODY, "b.py": _BODY})
    other_limits = DuplicationLimits(time_budget_secs=0, max_bucket_windows=128)
    assert load_pair_index(cache_dir, WINDOW, other_limits) is None

    (tmp_path / "a.py").write_text(_BODY.replace("doit", "edited"))
    parsed = _parsed(tmp_path)
    report = _incremental(parsed, cache_dir, {"a.py"}, limits=other_limits)
    assert "incremental" not in report.diagnostics
    assert _key(report) == _key(_full(parsed, limits=other_limits))


def test_missing_artifact_falls_back(tmp_path: Path):
    cache_dir = _seed(tmp_path, {"a.py": _BODY, "b.py": _BODY})
    (cache_dir / _INDEX_FILENAME).unlink()

    parsed = _parsed(tmp_path)
    report = _incremental(parsed, cache_dir, {"a.py"})
    assert "incremental" not in report.diagnostics
    assert _key(report) == _key(_full(parsed))
    # The fallback full run re-persists the artifact for next time.
    assert (cache_dir / _INDEX_FILENAME).exists()


def test_corrupt_artifact_falls_back(tmp_path: Path):
    cache_dir = _seed(tmp_path, {"a.py": _BODY, "b.py": _BODY})
    (cache_dir / _INDEX_FILENAME).write_bytes(b"not a pickle")

    parsed = _parsed(tmp_path)
    report = _incremental(parsed, cache_dir, {"a.py"})
    assert "incremental" not in report.diagnostics
    assert _key(report) == _key(_full(parsed))


def test_truncated_state_refuses_to_splice(tmp_path: Path):
    """A window-budget-hit run persists a flag that blocks splicing."""
    # Budget admits the first file's windows but trips on the second, so
    # the run persists a truncated state (an empty-window run would
    # return before persisting anything).
    lim = DuplicationLimits(time_budget_secs=0, max_total_windows=35)
    files = {"a.py": _BODY, "b.py": _BODY, "c.py": _BODY}
    cache_dir = _seed(tmp_path, files, limits=lim)
    idx = load_pair_index(cache_dir, WINDOW, lim)
    assert idx is not None and idx.window_budget_hit

    (tmp_path / "a.py").write_text(_BODY.replace("doit", "edited"))
    parsed = _parsed(tmp_path)
    report = _incremental(parsed, cache_dir, {"a.py"}, limits=lim)
    assert "incremental" not in report.diagnostics
    assert _key(report) == _key(_full(parsed, limits=lim))


def test_unchanged_token_cache_entries_survive_splice(tmp_path: Path):
    """The splice path must retain unchanged files' cache entries."""
    import hashlib

    from repowise.core.analysis.health.duplication.token_cache import (
        DuplicationTokenCache,
    )

    cache_dir = _seed(tmp_path, {"a.py": _BODY, "b.py": _BODY, "c.py": _OTHER})
    (tmp_path / "a.py").write_text(_BODY.replace("doit", "edited"))
    parsed = _parsed(tmp_path)
    report = _incremental(parsed, cache_dir, {"a.py"})
    assert report.diagnostics.get("incremental") is True

    cache = DuplicationTokenCache(cache_dir, WINDOW)
    cache.load()
    for rel in ("a.py", "b.py", "c.py"):
        digest = hashlib.sha256((tmp_path / rel).read_bytes()).hexdigest()
        assert cache.get(digest) is not None, rel


def test_co_change_weight_applied_live_on_splice(tmp_path: Path):
    """Finalize must consume the CURRENT git_meta_map, not persisted state."""
    import json

    cache_dir = _seed(tmp_path, {"a.py": _BODY, "b.py": _BODY, "c.py": _OTHER})
    (tmp_path / "c.py").write_text(_OTHER.replace("other", "edited"))
    parsed = _parsed(tmp_path)
    meta = {
        "a.py": {
            "co_change_partners_json": json.dumps([{"file_path": "b.py", "co_change_count": 7}])
        }
    }
    report = detect_clones(
        parsed,
        meta,
        window_tokens=WINDOW,
        min_lines=MIN_LINES,
        limits=LIMITS,
        cache_dir=cache_dir,
        changed_files={"c.py"},
    )
    assert report.diagnostics.get("incremental") is True
    ab = [p for p in report.pairs if {p.file_a, p.file_b} == {"a.py", "b.py"}]
    assert ab and all(p.co_change_count == 7 for p in ab)
