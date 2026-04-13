"""Validate core pipeline quality improvements.

Run against any repo to check:
  1. Call resolution — no cross-language edges, no builtin calls
  2. Heritage — no builtin parents (Exception, Object, etc.)
  3. Community detection — no generic labels, test/prod separation
  4. Execution flows — no demo/test entry points, reasonable depth

Usage:
    python scripts/validate_quality.py <repo_path>
    python scripts/validate_quality.py .
    python scripts/validate_quality.py test-repos/microdot
"""

from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path

from repowise.core.ingestion import ASTParser, FileTraverser, GraphBuilder
from repowise.core.ingestion.language_data import BUILTIN_CALLS, BUILTIN_PARENTS, get_builtin_calls


def main() -> None:
    repo_path = Path(sys.argv[1] if len(sys.argv) > 1 else ".").resolve()
    print(f"\n{'='*70}")
    print(f"  Validating: {repo_path.name}")
    print(f"{'='*70}\n")

    # --- Parse ---
    traverser = FileTraverser(repo_path)
    parser = ASTParser()
    parsed_files = []
    for fi in traverser.traverse():
        try:
            pf = parser.parse_file(fi, (repo_path / fi.path).read_bytes())
            if pf:
                parsed_files.append(pf)
        except Exception:
            pass

    print(f"Parsed {len(parsed_files)} files")

    # --- Check 1: Builtin call filtering ---
    print(f"\n--- CHECK 1: Builtin Call Filtering ---")
    total_calls = 0
    builtin_leaks = []
    for pf in parsed_files:
        builtins = get_builtin_calls(pf.file_info.language)
        for call in pf.calls:
            total_calls += 1
            if call.target_name in builtins:
                builtin_leaks.append(
                    f"  {pf.file_info.path}:{call.line} -> {call.target_name}()"
                )
    if builtin_leaks:
        print(f"  FAIL: {len(builtin_leaks)} builtin calls leaked through:")
        for leak in builtin_leaks[:10]:
            print(leak)
    else:
        print(f"  PASS: {total_calls} calls extracted, 0 builtins leaked")

    # --- Check 2: Heritage builtin filtering ---
    print(f"\n--- CHECK 2: Heritage Builtin Filtering ---")
    total_heritage = 0
    heritage_leaks = []
    for pf in parsed_files:
        parents = BUILTIN_PARENTS.get(pf.file_info.language, frozenset())
        for h in pf.heritage:
            total_heritage += 1
            if h.parent_name in parents:
                heritage_leaks.append(
                    f"  {pf.file_info.path}:{h.line} {h.child_name} -> {h.parent_name}"
                )
    if heritage_leaks:
        print(f"  FAIL: {len(heritage_leaks)} builtin parents leaked through:")
        for leak in heritage_leaks[:10]:
            print(leak)
    else:
        print(f"  PASS: {total_heritage} heritage relations, 0 builtin parents leaked")

    # --- Build graph ---
    builder = GraphBuilder(repo_path)
    for pf in parsed_files:
        builder.add_file(pf)
    graph = builder.build()

    # --- Check 3: Cross-language call edges ---
    print(f"\n--- CHECK 3: Cross-Language Call Edges ---")
    cross_lang_edges = []
    for u, v, d in graph.edges(data=True):
        if d.get("edge_type") != "calls":
            continue
        u_data = graph.nodes.get(u, {})
        v_data = graph.nodes.get(v, {})
        u_lang = u_data.get("language")
        v_lang = v_data.get("language")
        if u_lang and v_lang and u_lang != v_lang:
            conf = d.get("confidence", 0)
            cross_lang_edges.append(f"  [{conf:.2f}] {u} -> {v} ({u_lang}->{v_lang})")

    call_edges = sum(1 for _, _, d in graph.edges(data=True) if d.get("edge_type") == "calls")
    if cross_lang_edges:
        print(f"  WARN: {len(cross_lang_edges)} cross-language call edges (of {call_edges} total):")
        for edge in cross_lang_edges[:10]:
            print(edge)
    else:
        print(f"  PASS: {call_edges} call edges, 0 cross-language")

    # --- Check 4: Community detection quality ---
    print(f"\n--- CHECK 4: Community Detection ---")
    cd = builder.community_detection()
    info = builder.community_info()

    generic_labels = {"packages", "src", "lib", "core", "common", "app", ""}
    bad_labels = []
    test_dominated = []
    for cid, ci in sorted(info.items(), key=lambda x: -x[1].size):
        if ci.size <= 1:
            continue
        if ci.label.lower() in generic_labels or ci.label.startswith("cluster_"):
            bad_labels.append(f"  Community {cid}: label='{ci.label}' size={ci.size}")

        test_count = sum(1 for m in ci.members if "test" in m.lower())
        prod_count = ci.size - test_count
        if test_count > prod_count and ci.size > 3:
            test_dominated.append(
                f"  Community {cid} '{ci.label}': {test_count} test / {prod_count} prod files"
            )

    total_communities = sum(1 for ci in info.values() if ci.size > 1)
    print(f"  {total_communities} non-singleton communities detected")

    if bad_labels:
        print(f"  WARN: {len(bad_labels)} communities with generic labels:")
        for bl in bad_labels[:5]:
            print(bl)
    else:
        print(f"  PASS: All communities have meaningful labels")

    if test_dominated:
        print(f"  WARN: {len(test_dominated)} test-dominated communities:")
        for td in test_dominated[:5]:
            print(td)
    else:
        print(f"  PASS: No test-dominated communities")

    # Print top 10 communities
    print(f"\n  Top communities:")
    for cid, ci in sorted(info.items(), key=lambda x: -x[1].size)[:10]:
        if ci.size <= 1:
            continue
        print(f"    [{cid}] {ci.label:20s}  size={ci.size:3d}  cohesion={ci.cohesion:.3f}  lang={ci.dominant_language}")

    # --- Check 5: Execution flows ---
    print(f"\n--- CHECK 5: Execution Flows ---")
    try:
        report = builder.execution_flows()
        if report.flows:
            print(f"  {report.total_entry_points_scored} entry points scored, {report.total_flows} flows traced")
            for flow in report.flows[:5]:
                crosses = "CROSSES" if flow.crosses_community else "single"
                print(f"    {flow.entry_point_name:30s} score={flow.entry_point_score:.3f}  depth={flow.depth:2d}  {crosses}  communities={flow.communities_visited}")

            # Check for demo/test entry points
            bad_entries = [
                f for f in report.flows
                if any(x in f.entry_point_id.lower() for x in ("demo", "test", "fixture", "sample", "script"))
            ]
            if bad_entries:
                print(f"  WARN: {len(bad_entries)} demo/test/script entry points found")
            else:
                print(f"  PASS: No demo/test entry points in flows")

            # Check crosses_community
            crossing = [f for f in report.flows if f.crosses_community]
            print(f"  {len(crossing)}/{len(report.flows)} flows cross community boundaries")
        else:
            print(f"  No flows detected (may be too few call edges)")
    except Exception as exc:
        print(f"  ERROR: {exc}")

    # --- Summary ---
    print(f"\n{'='*70}")
    issues = len(builtin_leaks) + len(heritage_leaks) + len(cross_lang_edges) + len(bad_labels) + len(test_dominated)
    if issues == 0:
        print("  ALL CHECKS PASSED")
    else:
        print(f"  {issues} issue(s) found — see above")
    print(f"{'='*70}\n")


if __name__ == "__main__":
    main()
