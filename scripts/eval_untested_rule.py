"""Score the shared "untested" rule (``pr_blast.file_tests``) against hand labels.

The labels are ``tests/fixtures/untested_eval/labels.json``: about 150 code files
from this repository and two others, each labelled ``tested``, ``untested`` or
``nothing_to_test`` with the evidence. The README beside them says how they
were made. Each repository needs an index built at the commit the labels name.

    python scripts/eval_untested_rule.py \\
        --db repowise=path/to/repowise/.repowise/wiki.db \\
        --db gitleaks=path/to/gitleaks/.repowise/wiki.db \\
        --db caffeine=path/to/caffeine/.repowise/wiki.db

A repository without ``--db`` is skipped. ``--json PATH`` also writes every
file's verdict beside its label.

Reported, overall, per repository and per language, with 95% Wilson intervals:

- precision of "untested": of the files the rule calls untested, how many are.
- recall of real gaps: of the files labelled untested, how many it calls untested.
- false tested: files labelled untested that the rule calls tested.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
from collections import defaultdict
from pathlib import Path

LABELS = (
    Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "untested_eval" / "labels.json"
)


def wilson(hits: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """95% Wilson score interval for hits/n; (0, 1) when n is 0."""
    if n == 0:
        return 0.0, 1.0
    p = hits / n
    centre = (p + z * z / (2 * n)) / (1 + z * z / n)
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / (1 + z * z / n)
    return max(0.0, centre - half), min(1.0, centre + half)


def score(rows: list[dict]) -> dict:
    """Confusion counts and rates for ``rows`` (each has ``label`` and ``state``)."""
    claims = [r for r in rows if r["state"] == "untested"]
    gaps = [r for r in rows if r["label"] == "untested"]
    right = sum(r["label"] == "untested" for r in claims)
    found = sum(r["state"] == "untested" for r in gaps)
    false_tested = sum(r["state"] == "tested" for r in gaps)
    return {
        "files": len(rows),
        "claims": len(claims),
        "precision": (right, len(claims)),
        "recall": (found, len(gaps)),
        "false_tested": (false_tested, len(gaps)),
        "wrong_claims": len(claims) - right,
    }


def _fmt(pair: tuple[int, int]) -> str:
    hits, n = pair
    if n == 0:
        return "n/a"
    lo, hi = wilson(hits, n)
    return f"{hits}/{n} {hits / n:.0%} [{lo:.0%}-{hi:.0%}]"


def report(rows: list[dict]) -> str:
    groups: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        groups["all"].append(r)
        if r["drawn_from"] != "named":
            groups["random draws only"].append(r)
        groups[f"repo:{r['repo']}"].append(r)
        groups[f"lang:{r['language']}"].append(r)
    lines = [
        "| Slice | Files | Called untested | Precision of untested | Recall of real gaps | False tested |",
        "|---|---|---|---|---|---|",
    ]
    for name in sorted(groups, key=lambda g: (":" in g, g)):
        s = score(groups[name])
        lines.append(
            f"| {name} | {s['files']} | {s['claims']} | {_fmt(s['precision'])} "
            f"| {_fmt(s['recall'])} | {_fmt(s['false_tested'])} |"
        )
    return "\n".join(lines)


async def verdicts(db: str, paths: list[str]) -> dict[str, str]:
    """``file_tests`` state per path, from the index at *db*."""
    from sqlalchemy import text

    from repowise.core.analysis.pr_blast import file_tests
    from repowise.core.persistence.database import create_engine, create_session_factory

    engine = create_engine(f"sqlite+aiosqlite:///{db}")
    try:
        async with create_session_factory(engine)() as session:
            repo_id = (await session.execute(text("SELECT id FROM repositories"))).scalar_one()
            rows = await file_tests(session, repo_id, paths)
    finally:
        await engine.dispose()
    return {p: row.state for p, row in rows.items()}


async def record_slice(db: str, paths: list[str]) -> dict:
    """The graph rows ``file_tests`` reads for *paths*, for the checked-in regression slice.

    Every edge into (or, for ``defines``, out of) a node the walks ask about,
    with its type and origin, plus the file nodes it names and the test files.
    ``tests/unit/persistence/test_untested_eval_slice.py`` replays it, with no
    checkout, so a file recorded as type-only would replay as a claim: such files are refused.
    """
    from sqlalchemy import text

    from repowise.core.analysis.execution_graph import file_of_symbol
    from repowise.core.analysis.test_reach import db as reach_db
    from repowise.core.persistence.database import create_engine, create_session_factory

    asked: dict[str, set[str]] = {"into": set(), "from": set()}
    answer = reach_db._answer

    async def recording(need, session, repo_id, import_graph):
        asked["from" if need.kind == "declared" else "into"].update(need.keys)
        return await answer(need, session, repo_id, import_graph)

    engine = create_engine(f"sqlite+aiosqlite:///{db}")
    reach_db._answer = recording
    try:
        async with create_session_factory(engine)() as session:
            repo_id = (await session.execute(text("SELECT id FROM repositories"))).scalar_one()
            from repowise.core.analysis.pr_blast import file_tests
            from repowise.core.analysis.test_reach import clear_test_map_cache

            clear_test_map_cache()
            rows = await file_tests(session, repo_id, paths)
            edges: set[tuple] = set()
            for column, keys, extra in (
                ("target_node_id", asked["into"], "AND edge_type != 'co_changes'"),
                ("source_node_id", asked["from"], "AND edge_type = 'defines'"),
            ):
                keys = sorted(keys)
                for start in range(0, len(keys), 500):
                    batch = keys[start : start + 500]
                    params = {"r": repo_id, **{f"k{i}": k for i, k in enumerate(batch)}}
                    marks = ",".join(f":k{i}" for i in range(len(batch)))
                    found = await session.execute(
                        text(
                            "SELECT source_node_id, target_node_id, edge_type, resolution_origin, hint_source "
                            f"FROM graph_edges WHERE repository_id = :r AND {column} IN ({marks}) {extra}"
                        ),
                        params,
                    )
                    edges.update(tuple(r) for r in found)
            tests = {
                r[0]
                for r in await session.execute(
                    text(
                        "SELECT node_id FROM graph_nodes WHERE repository_id = :r AND is_test = 1"
                    ),
                    {"r": repo_id},
                )
            }
    finally:
        reach_db._answer = answer
        await engine.dispose()
    files = {file_of_symbol(n) for e in edges for n in e[:2]} | set(paths)
    named = {t for row in rows.values() for t in row.named_by}
    return {
        "states": {p: row.state for p, row in rows.items()},
        "files": sorted(f for f in files if f not in tests),
        "tests": sorted((files & tests) | named),
        "edges": sorted(list(e) for e in edges),
    }


def load_labels(path: Path = LABELS) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


async def run(dbs: dict[str, str]) -> list[dict]:
    files = load_labels()["files"]
    rows: list[dict] = []
    for repo, db in dbs.items():
        mine = [f for f in files if f["repo"] == repo]
        states = await verdicts(db, [f["path"] for f in mine])
        rows += [{**f, "state": states[f["path"]]} for f in mine]
    return rows


async def record(dbs: dict[str, str], out: str, ids: str | None) -> None:
    """Record the regression slice for *ids* (default: the ids already in *out*)."""
    wanted = (
        set(ids.split(","))
        if ids
        else {
            i
            for repo in json.loads(Path(out).read_text(encoding="utf-8"))["repos"].values()
            for i in repo["ids"]
        }
    )
    files = [f for f in load_labels()["files"] if f["id"] in wanted]
    missing = {f["repo"] for f in files} - set(dbs)
    if missing:
        raise SystemExit(
            f"--record needs a --db for every repository in the slice: {sorted(missing)}"
        )
    repos = {}
    for repo, db in dbs.items():
        mine = [f for f in files if f["repo"] == repo]
        if mine:
            repos[repo] = {
                "ids": sorted(f["id"] for f in mine),
                **await record_slice(db, [f["path"] for f in mine]),
            }
            claimed = {p for p, state in repos[repo]["states"].items() if state == "type_only"}
            if claimed:
                raise SystemExit(
                    f"type-only files cannot replay without a checkout: {sorted(claimed)}"
                )
    Path(out).write_text(json.dumps({"repos": repos}, indent=0) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--db",
        action="append",
        default=[],
        metavar="REPO=PATH",
        help="index of one labelled repository",
    )
    parser.add_argument(
        "--json", metavar="PATH", help="also write every file's verdict and label here"
    )
    parser.add_argument(
        "--record",
        metavar="PATH",
        help="write the regression slice (the labelled ids in it, or --slice) to PATH instead of scoring",
    )
    parser.add_argument("--slice", metavar="IDS", help="comma-separated label ids for a new slice")
    args = parser.parse_args()
    dbs = dict(item.split("=", 1) for item in args.db)
    if args.record:
        asyncio.run(record(dbs, args.record, args.slice))
        return
    rows = asyncio.run(run(dbs))
    print(report(rows))
    if args.json:
        Path(args.json).write_text(json.dumps(rows, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
