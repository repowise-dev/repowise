from dataclasses import replace
from pathlib import Path

from repowise.core.ingestion.graph import GraphBuilder
from repowise.core.ingestion.parser import ASTParser
from tests.unit.ingestion.test_rust_module_paths import _file_info, _imports


def test_only_source_relative_include_paths_are_extracted() -> None:
    imports = _imports(
        r"""
        include!("/generated.rs");
        include!(r"C:\build\generated.rs");
        include!(r"\\server\share\generated.rs");
        include!(r"C:generated.rs");
        include!("generated.rs");
        """
    )
    assert imports == [("./generated.rs", [])]


def test_literal_includes_reach_only_indexed_targets(tmp_path: Path) -> None:
    main = "src-tauri/src/main.rs"
    sources = {
        main: (
            "rust",
            """
            const SCRIPT: &str = include_str!("../../src/lib/inject.ts");
            const DATA: &[u8] = include_bytes!(r#"data.rs"#,);
            include!("generated.rs");
            const OTHER: &str = include_str!(concat!("ignored", ".rs"));
            const MISSING: &str = include_str!("missing.rs");
            const OUTSIDE: &str = include_str!("../../../src/lib/inject.ts");
            // include!("ignored.rs");
        """,
        ),
        "src/lib/inject.ts": ("typescript", "export const inject = true;"),
        "src-tauri/src/data.rs": ("rust", ""),
        "src-tauri/src/generated.rs": ("rust", ""),
        "src-tauri/src/ignored.rs": ("rust", ""),
    }
    builder = GraphBuilder(repo_path=tmp_path)
    parser = ASTParser()
    for path, (language, text) in sources.items():
        absolute = tmp_path / path
        absolute.parent.mkdir(parents=True, exist_ok=True)
        absolute.write_text(text, encoding="utf-8")
        info = replace(_file_info(path, str(absolute)), language=language)
        builder.add_file(parser.parse_file(info, text.encode()))

    graph = builder.build()
    imports = {
        target
        for _, target, data in graph.out_edges(main, data=True)
        if data["edge_type"] == "imports"
    }
    assert imports == {"src/lib/inject.ts", "src-tauri/src/data.rs", "src-tauri/src/generated.rs"}
