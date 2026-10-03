"""Cargo `[[bin]]`/`[[test]]`/`[[bench]]`/`[[example]]` targets with an explicit path.

Cargo discovers src/bin/*.rs, tests/*.rs, benches/*.rs and examples/*.rs by
convention; a target naming a non-default `path` is exactly the file Cargo
cannot find any other way, so it is the one case with no signal besides the
manifest itself (#2936).
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import ClassVar

import networkx as nx

from repowise.core.analysis.dead_code import DeadCodeAnalyzer, DeadCodeKind
from repowise.core.ingestion.graph import GraphBuilder
from repowise.core.ingestion.models import FileInfo
from repowise.core.ingestion.parser import ASTParser

_PARSER = ASTParser()


def _build(repo: Path, sources: dict[str, str]) -> nx.DiGraph:
    builder = GraphBuilder(repo_path=repo)
    for rel, body in sources.items():
        p = repo / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body, encoding="utf-8")
    for rel in sources:
        if rel.endswith(".rs"):
            abs_path = repo / rel
            file_info = FileInfo(
                path=rel,
                abs_path=str(abs_path.resolve()),
                language="rust",
                size_bytes=100,
                git_hash="",
                last_modified=datetime.now(),
                is_test=False,
                is_config=False,
                is_api_contract=False,
                is_entry_point=False,
            )
            builder.add_file(_PARSER.parse_file(file_info, abs_path.read_bytes()))
    return builder.build()


def _unused(graph: nx.DiGraph) -> set[str]:
    report = DeadCodeAnalyzer(graph, git_meta_map={}).analyze(
        {
            "detect_zombie_packages": False,
            "detect_unused_internals": False,
            "min_confidence": 0.0,
        }
    )
    return {
        f"{f.file_path}::{f.symbol_name}" if f.symbol_name else f.file_path
        for f in report.findings
        if f.kind in (DeadCodeKind.UNUSED_EXPORT, DeadCodeKind.UNREACHABLE_FILE)
    }


class TestWorkspaceMemberTargets:
    """A target table on a normally-walked workspace member."""

    _SOURCES: ClassVar[dict[str, str]] = {
        "Cargo.toml": (
            '[workspace]\nmembers = ["crates/net"]\n'
        ),
        "crates/net/Cargo.toml": (
            '[package]\nname = "net"\nversion = "0.1.0"\n\n'
            '[[bin]]\nname = "net_cli"\npath = "cli/main.rs"\n\n'
            '[[test]]\nname = "wire"\npath = "harness/wire.rs"\n'
        ),
        "crates/net/src/lib.rs": "pub fn connect() {}\n",
        "crates/net/cli/main.rs": "fn main() {}\n",
        "crates/net/harness/wire.rs": "pub fn target() {}\n",
    }

    def test_bin_with_an_explicit_path_is_an_entry_point(self, tmp_path: Path) -> None:
        graph = _build(tmp_path, self._SOURCES)
        node = graph.nodes["crates/net/cli/main.rs"]
        assert node.get("is_entry_point") is True

    def test_test_with_an_explicit_path_is_a_reachability_root_not_an_entry(
        self, tmp_path: Path
    ) -> None:
        graph = _build(tmp_path, self._SOURCES)
        node = graph.nodes["crates/net/harness/wire.rs"]
        assert node.get("is_reachability_root") is True
        assert not node.get("is_entry_point")

    def test_neither_target_file_is_reported_dead(self, tmp_path: Path) -> None:
        unused = _unused(_build(tmp_path, self._SOURCES))
        assert "crates/net/cli/main.rs" not in unused
        assert "crates/net/harness/wire.rs" not in unused
        assert "crates/net/harness/wire.rs::target" not in unused


class TestBenchAndExampleTargets:
    _SOURCES: ClassVar[dict[str, str]] = {
        "Cargo.toml": (
            '[package]\nname = "lib1"\nversion = "0.1.0"\n\n'
            '[[bench]]\nname = "throughput"\npath = "perf/throughput.rs"\n\n'
            '[[example]]\nname = "demo"\npath = "showcase/demo.rs"\n'
        ),
        "src/lib.rs": "pub fn work() {}\n",
        "perf/throughput.rs": "pub fn target() {}\n",
        "showcase/demo.rs": "fn main() {}\n",
    }

    def test_bench_and_example_targets_are_reachability_roots(self, tmp_path: Path) -> None:
        graph = _build(tmp_path, self._SOURCES)
        assert graph.nodes["perf/throughput.rs"].get("is_reachability_root") is True
        assert graph.nodes["showcase/demo.rs"].get("is_reachability_root") is True

    def test_neither_target_file_is_reported_dead(self, tmp_path: Path) -> None:
        unused = _unused(_build(tmp_path, self._SOURCES))
        assert "perf/throughput.rs" not in unused
        assert "showcase/demo.rs" not in unused


class TestTargetWithNoExplicitPathAddsNoRoot:
    """A bare ``[[bin]]`` (no ``path``) names nothing new; convention covers it."""

    _SOURCES: ClassVar[dict[str, str]] = {
        "Cargo.toml": '[package]\nname = "lib1"\nversion = "0.1.0"\n\n[[bin]]\nname = "lib1"\n',
        "src/lib.rs": "pub fn work() {}\n",
        "src/main.rs": "fn main() {}\n",
    }

    def test_build_does_not_crash_and_adds_no_bogus_root(self, tmp_path: Path) -> None:
        graph = _build(tmp_path, self._SOURCES)
        # The conventional src/main.rs is unaffected either way; the point is
        # a path-less entry must not be parsed into a (wrong) empty-string path.
        assert "" not in graph.nodes


class TestIsolatedCrateOutsideTheWorkspaceWalk:
    """The ripgrep ``fuzz/`` shape: a sub-crate with its own ``[workspace]``,
    excluded from the root's member list, naming a target by an explicit path.
    """

    _SOURCES: ClassVar[dict[str, str]] = {
        "Cargo.toml": (
            '[package]\nname = "ripgrep"\nversion = "0.1.0"\n\n'
            '[workspace]\nmembers = ["."]\nexclude = ["fuzz"]\n'
        ),
        "src/lib.rs": "pub fn run() {}\n",
        "fuzz/Cargo.toml": (
            '[package]\nname = "rg-fuzz"\nversion = "0.0.0"\n\n'
            "[workspace]\n\n"
            '[[bin]]\nname = "fuzz_glob"\npath = "fuzz_targets/fuzz_glob.rs"\n'
            "test = false\ndoc = false\n"
        ),
        "fuzz/fuzz_targets/fuzz_glob.rs": "pub fn target() {}\n",
    }

    def test_the_isolated_crates_bin_target_is_not_reported_dead(self, tmp_path: Path) -> None:
        unused = _unused(_build(tmp_path, self._SOURCES))
        assert "fuzz/fuzz_targets/fuzz_glob.rs" not in unused
        assert "fuzz/fuzz_targets/fuzz_glob.rs::target" not in unused

    def test_the_isolated_crates_bin_target_is_stamped_an_entry_point(
        self, tmp_path: Path
    ) -> None:
        graph = _build(tmp_path, self._SOURCES)
        assert graph.nodes["fuzz/fuzz_targets/fuzz_glob.rs"].get("is_entry_point") is True
