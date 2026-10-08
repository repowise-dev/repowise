"""A large class is served as an outline of its members, not its whole body."""

from __future__ import annotations

import pytest

from repowise.server.mcp_server import tool_symbol

_METHODS = 30
_METHOD_BODY = 14


def _big_class(offset: int = 0) -> tuple[str, list[tuple[str, int, int]]]:
    """Source of a class too large to serve whole, plus each method's bounds."""
    lines = [""] * offset + ["class Big:", '    """Holds many methods."""', ""]
    spans: list[tuple[str, int, int]] = []
    for i in range(_METHODS):
        start = len(lines) + 1
        lines.append(f"    def method_{i:02d}(self, value: int) -> int:")
        lines.append(f'        """Return value shifted by {i}."""')
        lines.extend(
            f"        value = value + {i} * {j}  # a line of ordinary body text" for j in range(_METHOD_BODY)
        )
        lines.append("        return value")
        spans.append((f"method_{i:02d}", start, len(lines)))
        lines.append("")
    lines.append("")
    lines.append("def big_function(value):")
    lines.extend(f"    value = value + {j}  # filler to make the function large" for j in range(400))
    lines.append("    return value")
    lines.append("")
    lines.append("class Small:")
    lines.append("    def one(self):")
    lines.append("        return 1")
    return "\n".join(lines) + "\n", spans


def _bounds(source: str, needle: str) -> int:
    return next(i for i, ln in enumerate(source.splitlines(), 1) if ln.startswith(needle))


@pytest.fixture
def big_repo(tmp_path, monkeypatch):
    import repowise.server.mcp_server as mcp_mod

    (tmp_path / "pkg").mkdir()
    monkeypatch.setattr(mcp_mod, "_repo_path", str(tmp_path))
    return tmp_path


async def _index(session, source: str, spans) -> None:
    from sqlalchemy import select

    from repowise.core.persistence.models import Repository, WikiSymbol

    repo = (await session.execute(select(Repository))).scalars().first()
    total = len(source.splitlines())
    big_start = _bounds(source, "class Big")
    fn_start = _bounds(source, "def big_function")
    small_start = _bounds(source, "class Small")

    def add(sid, name, kind, sig, start, end, parent=None, doc=None):
        session.add(
            WikiSymbol(
                repository_id=repo.id,
                file_path="pkg/big.py",
                symbol_id=f"pkg/big.py::{sid}",
                name=name,
                qualified_name=f"pkg.big.{sid.replace('::', '.')}",
                kind=kind,
                signature=sig,
                start_line=start,
                end_line=end,
                docstring=doc,
                language="python",
                parent_name=parent,
            )
        )

    add("Big", "Big", "class", "class Big", big_start, spans[-1][2], doc="Holds many methods.")
    for name, start, end in spans:
        add(
            f"Big::{name}",
            name,
            "method",
            f"def {name}(self, value: int) -> int",
            start,
            end,
            parent="Big",
            doc=f"Return value shifted by {name[-2:]}.",
        )
    add("big_function", "big_function", "function", "def big_function(value)", fn_start, small_start - 2)
    add("Small", "Small", "class", "class Small", small_start, total)
    add("Small::one", "one", "method", "def one(self)", small_start + 1, total, parent="Small")
    await session.flush()


@pytest.mark.asyncio
async def test_large_class_is_outlined_with_member_ids(setup_mcp, big_repo, session):
    from repowise.server.mcp_server import get_symbol

    source, spans = _big_class()
    (big_repo / "pkg" / "big.py").write_text(source)
    await _index(session, source, spans)

    result = await get_symbol("pkg/big.py::Big")

    assert result["outlined"] is True
    assert result["truncated"] is False
    assert "complete" not in result["_meta"]
    # Header only: declaration and docstring, no method bodies.
    assert "class Big:" in result["source"]
    assert "Holds many methods." in result["source"]
    assert "value = value" not in result["source"]
    assert result["symbol_start_line"] == 1
    assert result["symbol_end_line"] == spans[-1][2]

    members = result["members"]
    assert len(members) == _METHODS
    first = members[0]
    assert first["symbol_id"] == "pkg/big.py::Big::method_00"
    assert (first["start_line"], first["end_line"]) == spans[0][1:]
    assert first["signature"] == "def method_00(self, value: int) -> int"
    assert first["summary"] == "Return value shifted by 00."
    assert f"lines 1-{spans[-1][2]}" in result["note"]
    assert len(str(result)) < len(source) // 2

    # The member id is ready for a follow-up call that serves its whole body.
    method = await get_symbol(first["symbol_id"])
    assert "outlined" not in method
    assert "return value" in method["source"]
    assert method["_meta"]["complete"]


@pytest.mark.asyncio
async def test_drifted_members_are_relocated_with_one_parse(
    setup_mcp, big_repo, session, monkeypatch
):
    from repowise.server.mcp_server import get_symbol

    stale_source, spans = _big_class()
    await _index(session, stale_source, spans)
    live_source, live_spans = _big_class(offset=5)
    (big_repo / "pkg" / "big.py").write_text(live_source)

    calls = []
    real = tool_symbol.parse_live_symbols

    def counting(row, text):
        calls.append(row.symbol_id)
        return real(row, text)

    monkeypatch.setattr(tool_symbol, "parse_live_symbols", counting)
    result = await get_symbol("pkg/big.py::Big")

    assert result["outlined"] is True
    assert [(m["start_line"], m["end_line"]) for m in result["members"]] == [
        s[1:] for s in live_spans
    ]
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_small_class_and_function_keep_full_source(setup_mcp, big_repo, session):
    from repowise.server.mcp_server import get_symbol

    source, spans = _big_class()
    (big_repo / "pkg" / "big.py").write_text(source)
    await _index(session, source, spans)

    small = await get_symbol("pkg/big.py::Small")
    assert "outlined" not in small
    assert "return 1" in small["source"]
    assert small["_meta"]["complete"]

    # A function past the outline size still serves its body, as before.
    function = await get_symbol("pkg/big.py::big_function")
    assert "outlined" not in function
    assert "members" not in function
    assert "return value" in function["source"]
    assert len(function["source"]) > tool_symbol._OUTLINE_MIN_CHARS
