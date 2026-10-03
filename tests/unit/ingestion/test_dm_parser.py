from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from repowise.core.ingestion import FileTraverser, GraphBuilder
from repowise.core.ingestion.models import FileInfo
from repowise.core.ingestion.parser import ASTParser


def _file(path: str = "code/sample.dm") -> FileInfo:
    return FileInfo(
        path=path,
        abs_path=f"/repo/{path}",
        language="dm",
        size_bytes=0,
        git_hash="",
        last_modified=datetime.now(UTC),
        is_test=False,
        is_config=False,
        is_api_contract=False,
        is_entry_point=path.endswith(".dme"),
    )


def test_extracts_types_procs_and_globals() -> None:
    source = b"""\
var/global/combat_enabled = 1

/mob/player
\tproc/takeDamage(amount)
\t\tapplyDamage(amount)

/proc/applyDamage(amount)
\treturn amount
"""

    parsed = ASTParser().parse_file(_file(), source)

    assert ("class", "/mob/player") in {(s.kind, s.name) for s in parsed.symbols}
    assert ("variable", "combat_enabled") in {(s.kind, s.name) for s in parsed.symbols}
    assert ("method", "takeDamage") in {(s.kind, s.name) for s in parsed.symbols}
    assert ("function", "applyDamage") in {(s.kind, s.name) for s in parsed.symbols}
    assert parsed.calls == []


def test_extracts_dme_includes() -> None:
    source = b'#include "src\\Code\\Combat\\SpeedDelay.dm"\n'

    parsed = ASTParser().parse_file(_file("DU.dme"), source)

    assert [imp.module_path for imp in parsed.imports] == ["src\\Code\\Combat\\SpeedDelay.dm"]


def test_caps_legacy_parse_error_context_without_dropping_symbols() -> None:
    source = (
        (b"invalid syntax that the grammar cannot recover cleanly\n" * 200)
        + b"""\
/proc/stillIndexed()
\treturn 1
"""
    )

    parsed = ASTParser().parse_file(_file(), source)

    assert len(parsed.parse_errors) <= 21
    assert any("additional DM parse errors" in error for error in parsed.parse_errors)
    assert any(symbol.name == "stillIndexed" for symbol in parsed.symbols)


def test_discovers_dm_project_and_builds_include_edges(tmp_path: Path) -> None:
    sources = {
        "game.dme": '#include "code\\player.dm"\n#include "interface.dmf"\n',
        "code/player.dm": '#include "../shared.dm"\n/mob/player\n\tproc/play()\n\t\thelper()\n',
        "shared.dm": "/proc/helper()\n\treturn 1\n",
        "interface.dmf": 'window "main"\n',
    }
    for name, source in sources.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source, encoding="utf-8")

    discovered = {info.path: info for info in FileTraverser(tmp_path).traverse()}
    assert set(discovered) == set(sources)
    assert discovered["game.dme"].is_entry_point
    assert discovered["code/player.dm"].language == "dm"
    assert discovered["interface.dmf"].language == "dmf"

    parser = ASTParser()
    builder = GraphBuilder(repo_path=tmp_path)
    for info in discovered.values():
        parsed = parser.parse_file(info, Path(info.abs_path).read_bytes())
        if info.language == "dmf":
            assert parsed.symbols == []
            assert parsed.parse_errors == []
        builder.add_file(parsed)

    graph = builder.build()
    includes = {
        (source, target)
        for source, target, data in graph.edges(data=True)
        if data.get("edge_type") == "imports"
    }
    assert includes == {
        ("game.dme", "code/player.dm"),
        ("game.dme", "interface.dmf"),
        ("code/player.dm", "shared.dm"),
    }
    assert not any(data.get("edge_type") == "calls" for *_, data in graph.edges(data=True))
