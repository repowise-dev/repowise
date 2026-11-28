"""An Objective-C ``#import`` / ``#include`` resolves by the C/C++ include rule.

An include names one file. A header include never lands on the translation
unit that shares its stem, whether the includer is C++ or Objective-C++.
"""

from __future__ import annotations

from pathlib import Path

import networkx as nx

from repowise.core.ingestion.resolvers import resolve_import
from repowise.core.ingestion.resolvers.context import ResolverContext
from repowise.core.pipeline.incremental import build_repo_graph


def _ctx(tmp_path: Path, stem_map: dict[str, list[str]]) -> ResolverContext:
    paths = {p for bucket in stem_map.values() for p in bucket}
    return ResolverContext(
        path_set=paths, stem_map=stem_map, graph=nx.DiGraph(), repo_path=tmp_path
    )


def test_a_header_import_never_lands_on_a_same_stem_source(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path, {"os": ["core/os/os.cpp"], "view": ["platform/view.mm"]})

    assert resolve_import("os.h", "platform/view.mm", "objectivec", ctx) is None
    assert resolve_import("<os.h>", "platform/view.mm", "objectivec", ctx) is None


def test_a_header_import_resolves_to_the_header_it_names(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path, {"os": ["core/os/os.cpp", "core/os/os.h"]})

    assert resolve_import("core/os/os.h", "platform/view.mm", "objectivec", ctx) == "core/os/os.h"
    assert resolve_import("os.h", "platform/view.mm", "objectivec", ctx) == "core/os/os.h"


def test_an_objective_cxx_file_gets_no_edge_to_a_source_it_never_includes(tmp_path: Path) -> None:
    files = {
        "core/os.h": "#pragma once\nint os_time();\n",
        "core/os.cpp": '#include "os.h"\nint os_time() { return 1; }\n',
        "core/str.cpp": "int str_len() { return 0; }\n",
        "platform/view.h": "#import <Foundation/Foundation.h>\n@interface View : NSObject\n@end\n",
        "platform/view.mm": (
            '#import "view.h"\n'
            '#include "../core/os.h"\n'
            '#include "str.h"\n'
            "@implementation View\n"
            "- (int)now { return os_time(); }\n"
            "@end\n"
        ),
    }
    for rel, text in files.items():
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)

    graph = build_repo_graph(tmp_path, [])[2].graph()
    imports = {
        (u, v)
        for u, v, d in graph.edges(data=True)
        if u == "platform/view.mm" and d.get("edge_type", "imports") == "imports"
    }

    assert ("platform/view.mm", "core/os.h") in imports
    assert ("platform/view.mm", "core/os.cpp") not in imports
    assert ("platform/view.mm", "core/str.cpp") not in imports
