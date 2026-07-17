"""Regression test for #648: false-positive unreachable files for TS/JS path aliases."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from repowise.core.ingestion.graph import GraphBuilder
from repowise.core.ingestion.models import FileInfo, Import, ParsedFile
from repowise.core.ingestion import wire_tsconfig_resolver


def test_tsconfig_alias_cli_rebuild(tmp_path: Path) -> None:
    # 1. Setup mock tsconfig.json
    tsconfig_file = tmp_path / "tsconfig.json"
    tsconfig_file.write_text(
        json.dumps({
            "compilerOptions": {
                "baseUrl": ".",
                "paths": {"@/*": ["./src/*"]},
            }
        }),
        encoding="utf-8",
    )

    # 2. Setup mock files
    layout_path = "src/routes/layout.tsx"
    sensor_path = "src/components/SensorProvider.tsx"

    # Mock ParsedFile for layout.tsx (imports SensorProvider via alias)
    layout_import = Import(
        raw_statement="import Sensor from '@/components/SensorProvider'",
        module_path="@/components/SensorProvider",
        imported_names=["Sensor"],
        is_relative=False,
        resolved_file=None,
    )
    layout_parsed = ParsedFile(
        file_info=FileInfo(
            path=layout_path,
            abs_path=str(tmp_path / layout_path),
            language="typescript",
            size_bytes=100,
            git_hash="",
            last_modified=datetime.now(),
            is_test=False,
            is_config=False,
            is_api_contract=False,
            is_entry_point=True,
        ),
        symbols=[],
        imports=[layout_import],
        exports=[],
        docstring=None,
        parse_errors=[],
        content_hash="",
    )

    # Mock ParsedFile for SensorProvider.tsx (the target)
    sensor_parsed = ParsedFile(
        file_info=FileInfo(
            path=sensor_path,
            abs_path=str(tmp_path / sensor_path),
            language="typescript",
            size_bytes=100,
            git_hash="",
            last_modified=datetime.now(),
            is_test=False,
            is_config=False,
            is_api_contract=False,
            is_entry_point=False,
        ),
        symbols=[],
        imports=[],
        exports=[],
        docstring=None,
        parse_errors=[],
        content_hash="",
    )

    # 3. Build graph (mimic CLI command)
    graph_builder = GraphBuilder(repo_path=tmp_path)
    graph_builder.add_file(layout_parsed)
    graph_builder.add_file(sensor_parsed)

    # The fix: call wire_tsconfig_resolver before build
    wire_tsconfig_resolver(
        graph_builder, tmp_path, include_submodules=False, include_nested_repos=False
    )

    graph_builder.build()

    # 4. Assert SensorProvider is reached
    assert graph_builder.graph().has_edge(layout_path, sensor_path)
    assert graph_builder.graph().in_degree(sensor_path) > 0
