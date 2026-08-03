#!/usr/bin/env python3
"""Extract data from fastapi/.repowise/wiki.db into static JSON files for the hosted frontend demo."""

import json
import sqlite3
from collections import defaultdict
from pathlib import Path

DB_PATH = Path(__file__).parent.parent / "fastapi" / ".repowise" / "wiki.db"
OUT_DIR = Path(__file__).parent.parent / "repowise-hosted-frontend" / "public" / "data" / "fastapi"
REPO_ID = "4912f43143cb4cb881ee805268de797c"


def write_json(filename: str, data):
    path = OUT_DIR / filename
    with open(path, "w") as f:
        json.dump(data, f, separators=(",", ":"), default=str)
    size_kb = path.stat().st_size / 1024
    print(f"  {filename}: {size_kb:.1f} KB")


def extract_repo(cur):
    row = cur.execute(
        "SELECT id, name, url, local_path, default_branch, head_commit, settings_json, created_at, updated_at FROM repositories WHERE id = ?",
        (REPO_ID,),
    ).fetchone()
    if not row:
        raise RuntimeError(f"Repo {REPO_ID} not found")
    repo = {
        "id": row[0],
        "name": row[1] or "fastapi",
        "url": row[2] or "https://github.com/tiangolo/fastapi",
        "local_path": row[3],
        "default_branch": row[4],
        "head_commit": row[5],
        "settings": json.loads(row[6]) if row[6] else {},
        "created_at": row[7],
        "updated_at": row[8],
    }
    write_json("repo.json", repo)
    return repo


def extract_stats(cur):
    file_count = cur.execute(
        "SELECT COUNT(*) FROM graph_nodes WHERE repository_id = ? AND node_type = 'file'", (REPO_ID,)
    ).fetchone()[0]
    symbol_count = cur.execute(
        "SELECT COUNT(*) FROM wiki_symbols WHERE repository_id = ?", (REPO_ID,)
    ).fetchone()[0]
    entry_point_count = cur.execute(
        "SELECT COUNT(*) FROM graph_nodes WHERE repository_id = ? AND is_entry_point = 1", (REPO_ID,)
    ).fetchone()[0]

    total_pages = cur.execute(
        "SELECT COUNT(*) FROM wiki_pages WHERE repository_id = ?", (REPO_ID,)
    ).fetchone()[0]
    fresh_pages = cur.execute(
        "SELECT COUNT(*) FROM wiki_pages WHERE repository_id = ? AND freshness_status = 'fresh'", (REPO_ID,)
    ).fetchone()[0]
    doc_coverage = round((fresh_pages / total_pages * 100) if total_pages else 0, 1)
    freshness_score = round(
        cur.execute(
            "SELECT AVG(confidence) FROM wiki_pages WHERE repository_id = ?", (REPO_ID,)
        ).fetchone()[0]
        or 0,
        2,
    )
    dead_export_count = cur.execute(
        "SELECT COUNT(*) FROM dead_code_findings WHERE repository_id = ? AND kind = 'unused_export'", (REPO_ID,)
    ).fetchone()[0]

    stats = {
        "file_count": file_count,
        "symbol_count": symbol_count,
        "entry_point_count": entry_point_count,
        "doc_coverage_pct": doc_coverage,
        "freshness_score": freshness_score,
        "dead_export_count": dead_export_count,
    }
    write_json("stats.json", stats)


def extract_pages(cur):
    rows = cur.execute(
        """SELECT id, repository_id, page_type, title, content, target_path, source_hash,
                  model_name, provider_name, input_tokens, output_tokens, cached_tokens,
                  generation_level, version, confidence, freshness_status, metadata_json,
                  created_at, updated_at
           FROM wiki_pages WHERE repository_id = ?
           ORDER BY target_path""",
        (REPO_ID,),
    ).fetchall()

    pages_index = []
    pages_content = []
    for r in rows:
        meta = json.loads(r[16]) if r[16] else {}
        page = {
            "id": r[0],
            "repository_id": r[1],
            "page_type": r[2],
            "title": r[3],
            "content": r[4],
            "target_path": r[5],
            "source_hash": r[6],
            "model_name": r[7],
            "provider_name": r[8],
            "input_tokens": r[9],
            "output_tokens": r[10],
            "cached_tokens": r[11],
            "generation_level": r[12],
            "version": r[13],
            "confidence": r[14],
            "freshness_status": r[15],
            "metadata": meta,
            "created_at": r[17],
            "updated_at": r[18],
        }
        pages_content.append(page)
        # Index entry: same but without content
        idx = {k: v for k, v in page.items() if k != "content"}
        pages_index.append(idx)

    write_json("pages-index.json", pages_index)
    write_json("pages-content.json", pages_content)
    print(f"  -> {len(pages_content)} pages extracted")


def extract_graph(cur):
    # Get set of documented paths
    doc_paths = set()
    for (tp,) in cur.execute(
        "SELECT target_path FROM wiki_pages WHERE repository_id = ?", (REPO_ID,)
    ):
        doc_paths.add(tp)

    nodes = []
    for r in cur.execute(
        """SELECT node_id, node_type, language, symbol_count, pagerank, betweenness,
                  community_id, is_test, is_entry_point
           FROM graph_nodes WHERE repository_id = ?""",
        (REPO_ID,),
    ):
        nodes.append({
            "node_id": r[0],
            "node_type": r[1],
            "language": r[2],
            "symbol_count": r[3],
            "pagerank": round(r[4], 6),
            "betweenness": round(r[5], 6),
            "community_id": r[6],
            "is_test": bool(r[7]),
            "is_entry_point": bool(r[8]),
            "has_doc": r[0] in doc_paths,
        })

    links = []
    for r in cur.execute(
        """SELECT source_node_id, target_node_id, imported_names_json
           FROM graph_edges WHERE repository_id = ?""",
        (REPO_ID,),
    ):
        names = json.loads(r[2]) if r[2] else []
        links.append({
            "source": r[0],
            "target": r[1],
            "imported_names": names,
        })

    graph = {"nodes": nodes, "links": links}
    write_json("graph.json", graph)
    print(f"  -> {len(nodes)} nodes, {len(links)} edges")


def extract_symbols(cur):
    rows = cur.execute(
        """SELECT id, repository_id, file_path, symbol_id, name, qualified_name, kind,
                  signature, start_line, end_line, docstring, visibility, is_async,
                  complexity_estimate, language, parent_name
           FROM wiki_symbols WHERE repository_id = ?
           ORDER BY file_path, start_line""",
        (REPO_ID,),
    ).fetchall()

    symbols = []
    for r in rows:
        symbols.append({
            "id": r[0],
            "repository_id": r[1],
            "file_path": r[2],
            "symbol_id": r[3],
            "name": r[4],
            "qualified_name": r[5],
            "kind": r[6],
            "signature": r[7],
            "start_line": r[8],
            "end_line": r[9],
            "docstring": r[10],
            "visibility": r[11],
            "is_async": bool(r[12]),
            "complexity_estimate": r[13],
            "language": r[14],
            "parent_name": r[15],
        })

    write_json("symbols.json", symbols)
    print(f"  -> {len(symbols)} symbols")


def extract_hotspots(cur):
    rows = cur.execute(
        """SELECT file_path, commit_count_90d, commit_count_30d, churn_percentile,
                  primary_owner_name, is_hotspot, is_stable, bus_factor, contributor_count,
                  lines_added_90d, lines_deleted_90d, avg_commit_size, commit_categories_json
           FROM git_metadata WHERE repository_id = ?
           ORDER BY churn_percentile DESC
           LIMIT 100""",
        (REPO_ID,),
    ).fetchall()

    hotspots = []
    for r in rows:
        cats = json.loads(r[12]) if r[12] else {}
        hotspots.append({
            "file_path": r[0],
            "commit_count_90d": r[1],
            "commit_count_30d": r[2],
            "churn_percentile": round(r[3], 4),
            "primary_owner": r[4],
            "is_hotspot": bool(r[5]),
            "is_stable": bool(r[6]),
            "bus_factor": r[7],
            "contributor_count": r[8],
            "lines_added_90d": r[9],
            "lines_deleted_90d": r[10],
            "avg_commit_size": round(r[11], 1),
            "commit_categories": cats,
        })

    write_json("hotspots.json", hotspots)
    print(f"  -> {len(hotspots)} hotspots")


def extract_git_summary(cur):
    total_files = cur.execute(
        "SELECT COUNT(*) FROM git_metadata WHERE repository_id = ?", (REPO_ID,)
    ).fetchone()[0]
    hotspot_count = cur.execute(
        "SELECT COUNT(*) FROM git_metadata WHERE repository_id = ? AND is_hotspot = 1", (REPO_ID,)
    ).fetchone()[0]
    stable_count = cur.execute(
        "SELECT COUNT(*) FROM git_metadata WHERE repository_id = ? AND is_stable = 1", (REPO_ID,)
    ).fetchone()[0]
    avg_churn = cur.execute(
        "SELECT AVG(churn_percentile) FROM git_metadata WHERE repository_id = ?", (REPO_ID,)
    ).fetchone()[0] or 0

    # Top owners
    owner_counts = defaultdict(int)
    for (owner,) in cur.execute(
        "SELECT primary_owner_name FROM git_metadata WHERE repository_id = ? AND primary_owner_name IS NOT NULL",
        (REPO_ID,),
    ):
        owner_counts[owner] += 1

    top_owners = sorted(owner_counts.items(), key=lambda x: x[1], reverse=True)[:10]
    top_owners_list = [
        {"name": name, "file_count": count, "pct": round(count / total_files * 100, 1)}
        for name, count in top_owners
    ]

    summary = {
        "total_files": total_files,
        "hotspot_count": hotspot_count,
        "stable_count": stable_count,
        "average_churn_percentile": round(avg_churn, 2),
        "top_owners": top_owners_list,
    }
    write_json("git-summary.json", summary)


def extract_ownership(cur):
    # Module-level ownership: group by top-level directory
    rows = cur.execute(
        """SELECT file_path, primary_owner_name, primary_owner_commit_pct, bus_factor
           FROM git_metadata WHERE repository_id = ?""",
        (REPO_ID,),
    ).fetchall()

    module_map = defaultdict(lambda: {"files": [], "owners": defaultdict(int)})
    file_ownership = []

    for fp, owner, pct, bf in rows:
        parts = fp.split("/")
        module = parts[0] if len(parts) > 1 else "."

        module_map[module]["files"].append(fp)
        if owner:
            module_map[module]["owners"][owner] += 1

        file_ownership.append({
            "module_path": fp,
            "primary_owner": owner,
            "owner_pct": round(pct, 1) if pct else None,
            "file_count": 1,
            "is_silo": bf <= 1 if bf else False,
        })

    module_ownership = []
    for mod, data in sorted(module_map.items()):
        file_count = len(data["files"])
        owners = data["owners"]
        if owners:
            top_owner = max(owners, key=owners.get)
            top_pct = round(owners[top_owner] / file_count * 100, 1)
        else:
            top_owner = None
            top_pct = None
        is_silo = len(owners) <= 1
        module_ownership.append({
            "module_path": mod,
            "primary_owner": top_owner,
            "owner_pct": top_pct,
            "file_count": file_count,
            "is_silo": is_silo,
        })

    write_json("ownership-module.json", module_ownership)
    write_json("ownership-file.json", file_ownership)
    print(f"  -> {len(module_ownership)} modules, {len(file_ownership)} files")


def extract_dead_code(cur):
    rows = cur.execute(
        """SELECT id, kind, file_path, symbol_name, symbol_kind, confidence, reason,
                  lines, safe_to_delete, primary_owner, status, note
           FROM dead_code_findings WHERE repository_id = ?
           ORDER BY confidence DESC""",
        (REPO_ID,),
    ).fetchall()

    findings = []
    by_kind = defaultdict(int)
    conf_summary = {"high": 0, "medium": 0, "low": 0}
    deletable_lines = 0
    total_lines = 0

    for r in rows:
        conf = r[5]
        finding = {
            "id": r[0],
            "kind": r[1],
            "file_path": r[2],
            "symbol_name": r[3],
            "symbol_kind": r[4],
            "confidence": round(conf, 2),
            "reason": r[6],
            "lines": r[7],
            "safe_to_delete": bool(r[8]),
            "primary_owner": r[9],
            "status": r[10],
            "note": r[11],
        }
        findings.append(finding)
        by_kind[r[1]] += 1
        total_lines += r[7]
        if bool(r[8]):
            deletable_lines += r[7]
        if conf >= 0.8:
            conf_summary["high"] += 1
        elif conf >= 0.5:
            conf_summary["medium"] += 1
        else:
            conf_summary["low"] += 1

    write_json("dead-code.json", findings)
    write_json("dead-code-summary.json", {
        "total_findings": len(findings),
        "confidence_summary": conf_summary,
        "deletable_lines": deletable_lines,
        "total_lines": total_lines,
        "by_kind": dict(by_kind),
    })
    print(f"  -> {len(findings)} findings")


def extract_decisions(cur):
    rows = cur.execute(
        """SELECT id, repository_id, title, status, context, decision, rationale,
                  alternatives_json, consequences_json, affected_files_json,
                  affected_modules_json, tags_json, evidence_commits_json, source,
                  evidence_file, evidence_line, confidence, staleness_score,
                  superseded_by, last_code_change, created_at, updated_at
           FROM decision_records WHERE repository_id = ?
           ORDER BY confidence DESC""",
        (REPO_ID,),
    ).fetchall()

    decisions = []
    for r in rows:
        decisions.append({
            "id": r[0],
            "repository_id": r[1],
            "title": r[2],
            "status": r[3],
            "context": r[4],
            "decision": r[5],
            "rationale": r[6],
            "alternatives": json.loads(r[7]) if r[7] else [],
            "consequences": json.loads(r[8]) if r[8] else [],
            "affected_files": json.loads(r[9]) if r[9] else [],
            "affected_modules": json.loads(r[10]) if r[10] else [],
            "tags": json.loads(r[11]) if r[11] else [],
            "evidence_commits": json.loads(r[12]) if r[12] else [],
            "source": r[13],
            "evidence_file": r[14],
            "evidence_line": r[15],
            "confidence": round(r[16], 2),
            "staleness_score": round(r[17], 2),
            "superseded_by": r[18],
            "last_code_change": r[19],
            "created_at": r[20],
            "updated_at": r[21],
        })

    write_json("decisions.json", decisions)
    print(f"  -> {len(decisions)} decisions")


def main():
    print(f"Reading: {DB_PATH}")
    print(f"Output:  {OUT_DIR}")
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(str(DB_PATH))
    cur = conn.cursor()

    print("\nExtracting...")
    extract_repo(cur)
    extract_stats(cur)
    extract_pages(cur)
    extract_graph(cur)
    extract_symbols(cur)
    extract_hotspots(cur)
    extract_git_summary(cur)
    extract_ownership(cur)
    extract_dead_code(cur)
    extract_decisions(cur)

    conn.close()
    print("\nDone!")


if __name__ == "__main__":
    main()
