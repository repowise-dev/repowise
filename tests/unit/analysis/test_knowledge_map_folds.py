"""The knowledge-map folds over plain dicts, as a non-SQL producer passes them."""

from __future__ import annotations

from repowise.core.analysis.knowledge_map import onboarding_targets, rank_silos


def _git(path, pct, commits=0, hotspot=False, email="a@x.io"):
    return {
        "file_path": path,
        "primary_owner_email": email,
        "primary_owner_commit_pct": pct,
        "commit_count_90d": commits,
        "is_hotspot": hotspot,
    }


def test_rank_silos_filters_then_ranks_hotspot_activity_concentration() -> None:
    rows = [
        _git("quiet.py", 1.0),
        _git("shared.py", 0.8, commits=99, hotspot=True),
        _git("unknown.py", 0.9, commits=None),
        _git("busy.py", 0.85, commits=20),
        _git("hot.py", 0.81, commits=3, hotspot=True),
        _git("hot_tie.py", 0.95, commits=3, hotspot=True, email=None),
        _git("no_pct.py", None),
    ]
    out = rank_silos(rows)
    assert [s["file_path"] for s in out] == [
        "hot_tie.py",
        "hot.py",
        "busy.py",
        "quiet.py",
        "unknown.py",
    ]
    assert out[0] == {
        "file_path": "hot_tie.py",
        "owner_email": "",
        "owner_pct": 0.95,
        "commit_count_90d": 3,
        "is_hotspot": True,
    }
    assert out[-1]["commit_count_90d"] == 0


def test_onboarding_targets_skip_unranked_nodes_and_cap() -> None:
    nodes = [{"node_id": f"f{i}.py", "pagerank": 0.1 * (i + 1)} for i in range(12)]
    nodes.append({"node_id": "zero.py", "pagerank": 0.0})
    out = onboarding_targets(nodes, {"f11.py": 5, "f10.py": 2}, limit=10)
    assert len(out) == 10
    assert out[0] == {"path": "f9.py", "pagerank": 0.1 * 10, "doc_words": 0}
    everything = onboarding_targets(nodes, {"f11.py": 5, "f10.py": 2}, limit=20)
    assert len(everything) == 12
    tail = everything[-2:]
    assert [t["path"] for t in tail] == ["f10.py", "f11.py"]
