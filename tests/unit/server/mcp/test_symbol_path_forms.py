"""``get_symbol`` accepts the two path forms agents pass as ids.

``path:line`` (an editor or stack-trace location) reads as the innermost
indexed symbol holding that line, or the lines around it when none does. A
bare file path answers with the file's top-level symbol names to pass back,
not a body.
"""

from __future__ import annotations

import pytest

from repowise.core.persistence.models import WikiSymbol
from repowise.server.mcp_server import tool_symbol

# The fixture indexes src/auth/service.py: class AuthService (10-100) holding
# method login (20-40), and src/db/models.py: class User (5-30).


@pytest.fixture
def service_file(setup_mcp, tmp_path):
    lines = [f"# line {n}" for n in range(1, 201)]
    lines[9] = "class AuthService:"
    lines[19] = "    async def login(self, username: str, password: str) -> Token:"
    (tmp_path / "src" / "auth").mkdir(parents=True)
    (tmp_path / "src" / "auth" / "service.py").write_text("\n".join(lines) + "\n", "utf-8")


@pytest.mark.asyncio
async def test_a_path_line_id_reads_the_innermost_symbol_holding_it(service_file):
    from repowise.server.mcp_server import get_symbol

    method = await get_symbol("src/auth/service.py:25")
    assert method["symbol_id"] == "src/auth/service.py::login"
    assert (method["start_line"], method["end_line"]) == (20, 40)
    assert "error" not in method

    owner = await get_symbol("src/auth/service.py:60")
    assert owner["symbol_id"] == "src/auth/service.py::AuthService"


@pytest.mark.asyncio
async def test_a_path_line_id_matches_the_plain_symbol_read(service_file):
    from repowise.server.mcp_server import get_symbol

    by_line = await get_symbol("src/auth/service.py:25", depth=2)
    by_id = await get_symbol("src/auth/service.py::login", depth=2)
    by_line.pop("_meta")
    by_id.pop("_meta")
    assert by_line == by_id


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "symbol_id",
    ["src\\auth\\service.py:25", "./src/auth/service.py:25", "src/auth/service.py:25:9"],
    ids=["windows", "dot-prefix", "with-column"],
)
async def test_other_spellings_of_a_location_resolve_alike(service_file, symbol_id):
    from repowise.server.mcp_server import get_symbol

    result = await get_symbol(symbol_id)
    assert result["symbol_id"] == "src/auth/service.py::login"


@pytest.mark.asyncio
async def test_line_zero_is_never_owned_by_a_symbol_without_a_position(
    service_file, session, setup_mcp
):
    from repowise.server.mcp_server import get_symbol

    session.add(
        WikiSymbol(
            id="nopos",
            repository_id=setup_mcp,
            file_path="src/auth/service.py",
            symbol_id="src/auth/service.py::unplaced",
            name="unplaced",
            qualified_name="auth.service.unplaced",
            kind="function",
            signature="def unplaced()",
            start_line=0,
            end_line=0,
            language="python",
        )
    )
    await session.flush()
    result = await get_symbol("src/auth/service.py:0")
    assert result["kind"] == "range"
    assert result["start_line"] == 1


@pytest.mark.asyncio
async def test_a_line_outside_every_symbol_reads_the_lines_around_it(service_file):
    from repowise.server.mcp_server import get_symbol

    result = await get_symbol("src/auth/service.py:150")
    assert result["kind"] == "range"
    assert result["symbol_id"] == "src/auth/service.py:140-160"
    assert (result["start_line"], result["end_line"]) == (140, 160)

    top = await get_symbol("src/auth/service.py:3")
    assert (top["start_line"], top["end_line"]) == (1, 13)


@pytest.mark.asyncio
@pytest.mark.parametrize("symbol_id", ["src/auth/service.py:205", "src/auth/service.py:500"])
async def test_a_line_past_the_end_of_the_file_is_refused(service_file, symbol_id):
    """Not the file's last lines served in its place."""
    from repowise.server.mcp_server import get_symbol

    line = symbol_id.rsplit(":", 1)[1]
    result = await get_symbol(symbol_id)
    assert "source" not in result
    assert result["error"] == (
        f"Line {line} is past the end of 'src/auth/service.py' (200 lines)."
    )


@pytest.mark.asyncio
async def test_a_path_line_id_in_a_file_the_index_lacks(setup_mcp, tmp_path):
    from repowise.server.mcp_server import get_symbol

    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "new.py").write_text("a = 1\nb = 2\nc = 3\n", encoding="utf-8")
    result = await get_symbol("pkg/new.py:2")
    assert result["kind"] == "range"
    assert (result["start_line"], result["end_line"]) == (1, 3)


@pytest.mark.asyncio
@pytest.mark.parametrize("symbol_id", ["pkg/ghost.py:2", "Foo:3", "login:12"])
async def test_a_name_line_id_neither_indexed_nor_on_disk_is_a_symbol_miss(
    setup_mcp, symbol_id
):
    """Not a location: the symbol lookup's not-found reply, not a file-read error."""
    from repowise.server.mcp_server import get_symbol

    ctx = await tool_symbol._resolve_repo_context(None)
    assert await tool_symbol.ranges._line_id_target(symbol_id, ctx) is None
    result = await get_symbol(symbol_id)
    assert result.get("kind") != "range"
    assert result["error"].startswith(f"Symbol not found: {symbol_id!r}.")


@pytest.mark.asyncio
async def test_a_path_line_id_in_an_excluded_file_is_refused(service_file, monkeypatch):
    from repowise.server.mcp_server import get_symbol

    monkeypatch.setattr(
        tool_symbol, "is_excluded", lambda path, _spec: str(path).startswith("src/auth/")
    )
    result = await get_symbol("src/auth/service.py:25")
    assert result["error"] == "'src/auth/service.py' is excluded from indexing."


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "symbol_id",
    [
        "src/auth/service.py::login",
        "src/auth/service.py::AuthService::login",
        "src/auth/service.py::f:12",
        "Command::Find",
        "src/auth/service.py:20-40",
        "src/auth/service.py",
        "login",
    ],
)
async def test_other_ids_are_not_read_as_a_line(setup_mcp, symbol_id):
    ctx = await tool_symbol._resolve_repo_context(None)
    assert await tool_symbol.ranges._line_id_target(symbol_id, ctx) is None


@pytest.mark.asyncio
async def test_a_bare_file_path_lists_its_top_level_symbols(service_file):
    from repowise.server.mcp_server import get_symbol

    result = await get_symbol("src/auth/service.py")
    assert "error" not in result
    assert "source" not in result
    assert result["kind"] == "file"
    assert result["file"] == "src/auth/service.py"
    # login is a member of AuthService, so it is not top level.
    assert result["symbols"] == ["AuthService"]
    assert "symbols_total" not in result
    assert result["note"] == (
        'A file, not a symbol. One symbol: get_symbol("src/auth/service.py::<name>"). '
        'Whole file: get_context(targets=["src/auth/service.py"], include=["skeleton"]).'
    )

    windows = await get_symbol("src\\auth\\service.py")
    assert windows["symbols"] == ["AuthService"]


@pytest.mark.asyncio
async def test_a_bare_file_path_listing_is_capped_in_source_order(
    setup_mcp, session, tmp_path, monkeypatch
):
    from repowise.server.mcp_server import get_symbol
    from repowise.server.mcp_server.tool_context import targets

    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "many.py").write_text("\n" * 10, encoding="utf-8")
    for i in (3, 1, 2):
        session.add(
            WikiSymbol(
                id=f"many{i}",
                repository_id=setup_mcp,
                file_path="pkg/many.py",
                symbol_id=f"pkg/many.py::f{i}#{i}" if i == 2 else f"pkg/many.py::f{i}",
                name=f"f{i}",
                qualified_name=f"many.f{i}",
                kind="function",
                signature=f"def f{i}()",
                start_line=i,
                end_line=i,
                language="python",
            )
        )
    await session.flush()
    monkeypatch.setattr(targets, "_SYMBOL_CAP", 2)

    result = await get_symbol("pkg/many.py")
    # The id's own last segment, discriminator included, so path::<name> is exact.
    assert result["symbols"] == ["f1", "f2#2"]
    assert result["symbols_total"] == 3


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("suffix", "expected"),
    [
        ("::login", "src/auth/service.py::login"),
        (":25", "src/auth/service.py::login"),
        (":25:9", "src/auth/service.py::login"),
        (":20-40", "src/auth/service.py:20-40"),
    ],
    ids=["symbol", "line", "line-column", "range"],
)
@pytest.mark.parametrize("slashes", ["native", "forward"])
async def test_an_absolute_path_inside_the_repo_reads_as_its_relative_form(
    service_file, tmp_path, suffix, expected, slashes
):
    """Agents pass the path their editor or shell gave them, root and all."""
    from repowise.server.mcp_server import get_symbol

    path = str(tmp_path / "src" / "auth" / "service.py")
    if slashes == "forward":
        path = path.replace("\\", "/")
    result = await get_symbol(path + suffix)
    assert "error" not in result
    assert result["symbol_id"] == expected


@pytest.mark.asyncio
async def test_an_absolute_file_path_inside_the_repo_lists_its_symbols(service_file, tmp_path):
    from repowise.server.mcp_server import get_symbol

    result = await get_symbol(str(tmp_path / "src" / "auth" / "service.py"))
    assert result["file"] == "src/auth/service.py"
    assert result["symbols"] == ["AuthService"]


@pytest.mark.asyncio
@pytest.mark.parametrize("escape", [False, True], ids=["sibling", "dot-dot"])
async def test_an_absolute_path_outside_the_repo_stays_a_miss(service_file, tmp_path, escape):
    """Never resolved against the repo by its tail: the caller's id is named as given."""
    from repowise.server.mcp_server import get_symbol

    base = tmp_path / ".." / tmp_path.name / ".." if escape else tmp_path.parent
    outside = str(base / "elsewhere" / "src" / "auth" / "service.py") + "::login"
    result = await get_symbol(outside)
    assert "source" not in result
    assert result["error"].startswith(f"Symbol not found: {outside!r}.")
