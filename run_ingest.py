"""
Quick ingestion run on interview-coach repo (no LLM calls).
Prints a summary of what repowise discovered AND projects how many pages
would be generated (without actually calling any LLM).
"""
import logging
import os
import sys

os.environ.setdefault("PYTHONUTF8", "1")
logging.disable(logging.CRITICAL)

import structlog
structlog.configure(
    wrapper_class=structlog.make_filtering_bound_logger(logging.CRITICAL),
)

from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "packages" / "core" / "src"))

from repowise.core.ingestion.traverser import FileTraverser
from repowise.core.ingestion.parser import ASTParser
from repowise.core.ingestion.graph import GraphBuilder
from repowise.core.ingestion.models import ParsedFile
from repowise.core.generation.models import GenerationConfig
from repowise.core.generation.page_generator import (
    _CODE_LANGUAGES, _INFRA_LANGUAGES, _INFRA_FILENAMES, _is_infra_file, _is_significant_file
)

REPO_PATH = Path(r"C:\Users\ragha\Desktop\interview-coach")
config = GenerationConfig()  # defaults: file_page_top_percentile=0.20, top_symbol_percentile=0.10

# ---------------------------------------------------------------------------
# 1. Traverse + parse
# ---------------------------------------------------------------------------
print(f"Traversing {REPO_PATH} ...")
traverser = FileTraverser(REPO_PATH)
file_infos = list(traverser.traverse())
print(f"  {len(file_infos)} files found\n")

parser = ASTParser()
builder = GraphBuilder()
parsed_files: list[ParsedFile] = []

for fi in file_infos:
    try:
        src = Path(fi.abs_path).read_bytes()
        parsed = parser.parse_file(fi, src)
        builder.add_file(parsed)
        parsed_files.append(parsed)
    except Exception:
        pass

print(f"  {len(parsed_files)} files parsed\n")

# ---------------------------------------------------------------------------
# 2. Build graph + metrics
# ---------------------------------------------------------------------------
print("Building graph + metrics ...")
graph     = builder.build()
pagerank  = builder.pagerank()
betweenness = builder.betweenness_centrality()
sccs      = builder.strongly_connected_components()
print(f"  {graph.number_of_nodes()} nodes, {graph.number_of_edges()} edges\n")

# ---------------------------------------------------------------------------
# 3. Language breakdown
# ---------------------------------------------------------------------------
lang_counts = Counter(p.file_info.language for p in parsed_files)
print("Language breakdown (all files):")
for lang, n in lang_counts.most_common():
    marker = " [code]" if lang in _CODE_LANGUAGES else (" [infra]" if lang in _INFRA_LANGUAGES else " [skip]")
    print(f"  {lang:20s} {n:4d}{marker}")

# ---------------------------------------------------------------------------
# 4. Project page counts (no LLM)
# ---------------------------------------------------------------------------
print("\n--- Projected page counts (no LLM calls) ---\n")

# Level 0: api_contract
api_files = [p for p in parsed_files if p.file_info.is_api_contract]
print(f"Level 0  api_contract      : {len(api_files):4d} pages")

# Level 1: symbol_spotlight — top top_symbol_percentile public symbols by PageRank
all_public_syms = [
    (s, p) for p in parsed_files for s in p.symbols if s.visibility == "public"
]
n_spotlight = max(1, int(len(all_public_syms) * config.top_symbol_percentile)) if all_public_syms else 0
print(f"Level 1  symbol_spotlight  : {n_spotlight:4d} pages  ({len(all_public_syms)} public symbols × {config.top_symbol_percentile:.0%})")

# Level 2: file_page — significant code files only
code_files = [
    p for p in parsed_files
    if not p.file_info.is_api_contract
    and not _is_infra_file(p)
    and p.file_info.language in _CODE_LANGUAGES
]
code_pr_scores = sorted([pagerank.get(p.file_info.path, 0.0) for p in code_files], reverse=True)
n_top = max(1, int(len(code_pr_scores) * config.file_page_top_percentile))
pr_threshold = code_pr_scores[n_top - 1] if code_pr_scores else 0.0

sig_files = [
    p for p in code_files
    if _is_significant_file(p, pagerank, betweenness, config, pr_threshold)
]
skipped_no_symbols = sum(1 for p in code_files if len(p.symbols) < config.file_page_min_symbols)
skipped_non_code   = sum(1 for p in parsed_files
                         if not p.file_info.is_api_contract
                         and not _is_infra_file(p)
                         and p.file_info.language not in _CODE_LANGUAGES)
print(f"Level 2  file_page         : {len(sig_files):4d} pages  ({len(code_files)} code files, top {config.file_page_top_percentile:.0%} + entry points + bridges)")
print(f"           skipped (no symbols)   : {skipped_no_symbols}")
print(f"           skipped (non-code lang): {skipped_non_code}  (markdown, json, yaml, sql ...)")

# Level 3: scc_page
cycles = [s for s in sccs if len(s) > 1]
print(f"Level 3  scc_page          : {len(cycles):4d} pages  (circular dependency cycles)")

# Level 4: module_page — one per unique top-level directory of code files
module_groups: dict[str, int] = {}
for p in code_files:
    parts = Path(p.file_info.path).parts
    module = parts[0] if len(parts) > 1 else "root"
    module_groups[module] = module_groups.get(module, 0) + 1
print(f"Level 4  module_page       : {len(module_groups):4d} pages  (top-level dirs: {', '.join(sorted(module_groups)[:6])}{'...' if len(module_groups) > 6 else ''})")

# Level 5: cross_package (skip — monorepo only, not implemented yet)
print(f"Level 5  cross_package     :    0 pages  (Phase 4)")

# Level 6: repo_overview + architecture_diagram
print(f"Level 6  repo_overview     :    1 page")
print(f"Level 6  architecture_diag :    1 page")

# Level 7: infra_page
infra_files = [p for p in parsed_files if _is_infra_file(p)]
print(f"Level 7  infra_page        : {len(infra_files):4d} pages  (Dockerfile, Makefile, Terraform)")

total = (len(api_files) + n_spotlight + len(sig_files) + len(cycles)
         + len(module_groups) + 2 + len(infra_files))
print(f"\nTOTAL                      : {total:4d} pages")

# ---------------------------------------------------------------------------
# 5. Top 10 file pages that would be generated
# ---------------------------------------------------------------------------
print("\nTop 10 file_page candidates (by PageRank):")
sig_sorted = sorted(sig_files, key=lambda p: pagerank.get(p.file_info.path, 0.0), reverse=True)
for p in sig_sorted[:10]:
    syms = len(p.symbols)
    pr   = pagerank.get(p.file_info.path, 0.0)
    bc   = betweenness.get(p.file_info.path, 0.0)
    flag = " [entry]" if p.file_info.is_entry_point else (" [bridge]" if bc > 0 else "")
    print(f"  {pr:.4f}  {p.file_info.path}  ({syms} symbols){flag}")

print("\nDone — no LLM calls made.")
