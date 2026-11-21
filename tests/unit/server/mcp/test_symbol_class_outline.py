"""A class is served as an outline of its members, not its whole body."""

from __future__ import annotations

import json

import pytest

from repowise.server.mcp_server import tool_symbol

_METHODS = 30
_METHOD_BODY = 14


def _big_class(
    offset: int = 0, methods: int = _METHODS
) -> tuple[str, list[tuple[str, int, int]]]:
    """Source of a class too large to serve whole, plus each method's bounds.

    ``Big`` ends with a nested ``Inner``; ``Outer`` later in the file holds a
    second, nested ``Big`` whose members share the parent name.
    """
    lines = [""] * offset + ["class Big:", '    """Holds many methods."""', ""]
    spans: list[tuple[str, int, int]] = []
    for i in range(methods):
        start = len(lines) + 1
        lines.append(f"    def method_{i:03d}(self, value: int) -> int:")
        lines.append(f'        """Return value shifted by {i}."""')
        lines.extend(
            f"        value = value + {i} * {j}  # a line of ordinary body text"
            for j in range(_METHOD_BODY)
        )
        lines.append("        return value")
        spans.append((f"method_{i:03d}", start, len(lines)))
        lines.append("")
    lines.extend(["    class Inner:", "        def deep(self):", "            return 2", "", ""])
    lines.append("def big_function(value):")
    lines.extend(f"    value = value + {j}  # filler to make the function large" for j in range(400))
    lines.extend(["    return value", ""])
    lines.extend(["class Small:", "    def one(self):", "        return 1", ""])
    lines.extend(["class Outer:", "    class Big:", "        def stray(self):", "            return 0"])
    return "\n".join(lines) + "\n", spans


def _line(source: str, needle: str) -> int:
    return next(i for i, ln in enumerate(source.splitlines(), 1) if ln.startswith(needle))


@pytest.fixture
def big_repo(tmp_path, monkeypatch):
    import repowise.server.mcp_server as mcp_mod

    (tmp_path / "pkg").mkdir()
    monkeypatch.setattr(mcp_mod, "_repo_path", str(tmp_path))
    return tmp_path


async def _index(session, source: str, spans):
    """Index *source*'s symbols; returns ``add`` for extra rows."""
    from sqlalchemy import select

    from repowise.core.persistence.models import Repository, WikiSymbol

    repo = (await session.execute(select(Repository))).scalars().first()

    def add(sid, name, kind, sig, start, end, parent=None, doc=None):
        session.add(
            WikiSymbol(
                repository_id=repo.id,
                file_path="pkg/big.py",
                symbol_id=sid if ".py::" in sid else f"pkg/big.py::{sid}",
                name=name,
                qualified_name=f"pkg.big.{name}",
                kind=kind,
                signature=sig,
                start_line=start,
                end_line=end,
                docstring=doc,
                language="python",
                parent_name=parent,
            )
        )

    inner = _line(source, "    class Inner")
    fn_start = _line(source, "def big_function")
    small = _line(source, "class Small")
    outer = _line(source, "class Outer")
    add("Big", "Big", "class", "class Big", _line(source, "class Big"), inner + 2, doc="Holds many methods.")
    for name, start, end in spans:
        add(
            f"Big::{name}",
            name,
            "method",
            f"def {name}(self, value: int) -> int",
            start,
            end,
            parent="Big",
            doc=f"Return value shifted by {name[-3:]}.",
        )
    add("Big::Inner", "Inner", "class", "class Inner", inner, inner + 2, parent="Big")
    add("Big::Inner::deep", "deep", "method", "def deep(self)", inner + 1, inner + 2, parent="Inner")
    add("big_function", "big_function", "function", "def big_function(value)", fn_start, small - 2)
    add("Small", "Small", "class", "class Small", small, small + 2)
    add("Small::one", "one", "method", "def one(self)", small + 1, small + 2, parent="Small")
    add("Outer", "Outer", "class", "class Outer", outer, outer + 3)
    add("Outer::Big", "Big", "class", "class Big", outer + 1, outer + 3, parent="Outer")
    add("Outer::Big::stray", "stray", "method", "def stray(self)", outer + 2, outer + 3, parent="Big")
    await session.flush()
    return add


async def _setup(big_repo, session, **kwargs):
    source, spans = _big_class(**kwargs)
    (big_repo / "pkg" / "big.py").write_text(source)
    add = await _index(session, source, spans)
    return source, spans, add


@pytest.mark.asyncio
async def test_large_class_is_outlined_with_member_ids(setup_mcp, big_repo, session):
    from repowise.server.mcp_server import get_symbol

    source, spans, _ = await _setup(big_repo, session)
    big_end = _line(source, "    class Inner") + 2

    result = await get_symbol("pkg/big.py::Big")

    assert result["outlined"] is True
    assert result["truncated"] is False
    assert "complete" not in result["_meta"]
    assert "members_total" not in result
    # Header only: declaration and docstring, no method bodies.
    assert "class Big:" in result["source"]
    assert "Holds many methods." in result["source"]
    assert "value = value" not in result["source"]
    assert (result["symbol_start_line"], result["symbol_end_line"]) == (1, big_end)

    members = result["members"]
    assert len(members) == _METHODS + 1
    assert "more_members" not in result
    first = members[0]
    # The id is the class id plus the name, as the note says, so it is not repeated.
    assert first["name"] == "method_000"
    assert "symbol_id" not in first and "kind" not in first
    assert first["lines"] == "{}-{}".format(*spans[0][1:])
    assert first["signature"] == "def method_000(self, value: int) -> int"
    assert first["summary"] == "Return value shifted by 000."
    assert result["note"].startswith(f"Body (1-{big_end}) outlined")
    assert "get_symbol('pkg/big.py::Big::<name>')" in result["note"]
    assert f"lines 1-{big_end}" in result["note"]
    assert len(str(result)) < len(source) // 2

    # The member id is ready for a follow-up call that serves its whole body.
    method = await get_symbol("pkg/big.py::Big::method_000")
    assert "outlined" not in method
    assert "return value" in method["source"]
    assert method["_meta"]["complete"]


@pytest.mark.asyncio
async def test_nested_and_unverified_members_stay_in_their_own_class(
    setup_mcp, big_repo, session
):
    from repowise.server.mcp_server import get_symbol

    source, _spans, add = await _setup(big_repo, session)
    outer = _line(source, "class Outer")
    # Neither name is in the live file, so both stay unverified; only the one
    # indexed inside this Big belongs to it.
    add("Big::ghost_inside", "ghost_inside", "method", "def ghost_inside(self)", 4, 5, parent="Big")
    add(
        "Outer::Big::ghost_outside",
        "ghost_outside",
        "method",
        "def ghost_outside(self)",
        outer + 2,
        outer + 3,
        parent="Big",
    )
    await session.flush()

    result = await get_symbol("pkg/big.py::Big")

    by_name = {m["name"]: m for m in result["members"]}
    assert by_name["Inner"]["kind"] == "class"
    assert by_name["ghost_inside"]["bounds"] == "approximate"
    assert {"deep", "stray", "ghost_outside"}.isdisjoint(by_name)


@pytest.mark.asyncio
async def test_overload_rows_sharing_an_id_are_listed_once(setup_mcp, big_repo, session):
    from repowise.server.mcp_server import get_symbol

    _source, spans, add = await _setup(big_repo, session)
    _name, start, end = spans[0]
    add("./pkg/big.py::Big::method_000", "method_000", "method", "def method_000(self)", start, end, parent="Big")
    await session.flush()

    result = await get_symbol("pkg/big.py::Big")

    names = [m["name"] for m in result["members"]]
    assert names.count("method_000") == 1
    assert len(names) == len(set(names)) == _METHODS + 1


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
    methods = [m for m in result["members"] if "kind" not in m]
    assert [m["lines"] for m in methods] == ["{}-{}".format(*s[1:]) for s in live_spans]
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_small_class_and_function_keep_full_source(setup_mcp, big_repo, session):
    from repowise.server.mcp_server import get_symbol

    await _setup(big_repo, session)

    small = await get_symbol("pkg/big.py::Small")
    assert "outlined" not in small
    assert "return 1" in small["source"]
    assert small["_meta"]["complete"]

    # A function, however large, still serves its body.
    function = await get_symbol("pkg/big.py::big_function")
    assert "outlined" not in function
    assert "members" not in function
    assert "return value" in function["source"]


@pytest.mark.asyncio
async def test_a_class_of_a_few_methods_is_outlined_too(setup_mcp, big_repo, session):
    from repowise.server.mcp_server import get_symbol

    _source, spans, _ = await _setup(big_repo, session, methods=3)

    result = await get_symbol("pkg/big.py::Big")

    assert result["outlined"] is True
    assert [m["name"] for m in result["members"]][:3] == [s[0] for s in spans]
    assert "value = value" not in result["source"]


@pytest.mark.asyncio
async def test_very_large_class_caps_members_under_the_budget(setup_mcp, big_repo, session):
    from repowise.server.mcp_server import get_symbol, tool_middleware
    from repowise.server.mcp_server._budget.contracts import DEFAULT_RESPONSE_CHARS

    await _setup(big_repo, session, methods=250)

    result = await tool_middleware(get_symbol)(symbol_id="pkg/big.py::Big")

    assert "error" not in result
    assert result["outlined"] is True
    assert result["members_total"] == 251
    listed = len(result["members"])
    named = result["more_members"].split(", ")
    assert 0 < listed < listed + len(named) < 251
    # Past the full rows, members are named with their first line.
    assert named[0].startswith("method_") and named[0].split(" ")[1].isdigit()
    assert f"{251 - listed - len(named)} more members are not listed" in result["note"]
    assert f"after line {named[-1].split(' ')[1]}" in result["note"]
    assert len(json.dumps(result)) <= DEFAULT_RESPONSE_CHARS


@pytest.mark.asyncio
async def test_outline_skips_callee_bodies(setup_mcp, big_repo, session, monkeypatch):
    from repowise.server.mcp_server import get_symbol

    await _setup(big_repo, session)

    async def no_walk(*_args, **_kwargs):
        raise AssertionError("an outline must not expand callees")

    monkeypatch.setattr(tool_symbol, "_expand_callees", no_walk)
    result = await get_symbol("pkg/big.py::Big", depth=2)

    assert result["outlined"] is True
    assert "callee_bodies" not in result


@pytest.mark.asyncio
async def test_context_lines_never_tip_a_class_into_an_outline(setup_mcp, big_repo, session):
    from repowise.server.mcp_server import get_symbol

    await _setup(big_repo, session)
    bare = await get_symbol("pkg/big.py::Small")

    widened = await get_symbol("pkg/big.py::Small", context_lines=20)

    assert len(widened["source"]) > len(bare["source"])
    assert "outlined" not in widened


@pytest.mark.asyncio
async def test_context_lines_widen_the_outline_header_upward(setup_mcp, big_repo, session):
    from repowise.server.mcp_server import get_symbol

    await _setup(big_repo, session, offset=5)

    result = await get_symbol("pkg/big.py::Big", context_lines=3)

    assert result["outlined"] is True
    assert result["start_line"] == 3
    assert result["symbol_start_line"] == 6
    assert "value = value" not in result["source"]


@pytest.mark.asyncio
async def test_a_member_named_past_the_rows_keeps_an_id_that_is_not_its_name(
    setup_mcp, big_repo, session
):
    from repowise.server.mcp_server import get_symbol

    _source, spans, add = await _setup(big_repo, session, methods=120)
    _name, start, end = spans[-1]
    # An id that is not ``Big::<name>`` (an overload suffix, say).
    add("Big::method_119#2", "method_119", "method", "def method_119(self)", start, end, parent="Big")
    await session.flush()

    result = await get_symbol("pkg/big.py::Big")

    assert f"pkg/big.py::Big::method_119#2 {start}" in result["more_members"]
