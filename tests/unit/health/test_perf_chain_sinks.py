"""One query written as a chain of calls is one sink.

``(await session.execute(q)).scalars().all()`` is three calls the dialect
classifies as sinks and one round trip. Written on one line they collapsed to
one hit, which quoted the outermost call's method alone (``all``); with the
chain opened on its own line the outer call starts a line above the inner one
and they were two hits (#3267).

The innermost sink in a receiver chain is the call that leaves the process: it
is the hit, on its own line, and the links above it are neither hits nor
helpers the loop calls.
"""

from __future__ import annotations

import dataclasses

from repowise.core.analysis.health.complexity import walk_file


def _walk(source: str):
    return walk_file("t.py", "python", source.encode())


def _hits(source: str) -> list[tuple[str, int, str]]:
    return [(h.kind, h.line, h.sink_call) for h in _walk(source).perf_hits]


def _targets(source: str) -> set[str]:
    """Names the loop is recorded as calling, for cross-function reachability."""
    return {name for f in _walk(source).perf_fn_facts for name, _ in f.loop_call_targets}


_OVER_LINES = (
    "from sqlalchemy import select\n"
    "async def f(session, types):\n"
    "    for t in types:\n"
    "        rows = (\n"
    "            await session.execute(\n"
    "                select(Page.id).where(Page.t == t)\n"
    "            )\n"
    "        ).scalars().all()\n"
)
_ONE_LINE = (
    "from sqlalchemy import select\n"
    "async def f(session, types):\n"
    "    for t in types:\n"
    "        rows = (await session.execute(select(Page.id).where(Page.t == t))).scalars().all()\n"
)


def test_a_chain_opened_on_its_own_line_is_one_hit_on_the_call_that_does_the_io():
    assert _hits(_OVER_LINES) == [
        ("io_in_loop", 5, "session.execute"),
        ("serial_await_in_loop", 5, "session.execute"),
    ]


def test_the_same_chain_on_one_line_quotes_that_call_too():
    """It was one hit already, quoted as ``all``."""
    assert _hits(_ONE_LINE) == [
        ("io_in_loop", 4, "session.execute"),
        ("serial_await_in_loop", 4, "session.execute"),
    ]


def test_the_links_of_a_chain_are_not_helpers_the_loop_calls():
    """A link that is no longer a hit must not turn into a call target."""
    for source in (_OVER_LINES, _ONE_LINE):
        assert _targets(source) == {"select", "where"}


def test_a_chain_that_reads_well_as_written_keeps_its_whole_text():
    """``conn.execute(q).fetchone()`` starts with a name, so the quote is the
    chain with its arguments dropped, as it was."""
    source = (
        "from sqlalchemy import select\n"
        "def f(conn, session, keys):\n"
        "    for key in keys:\n"
        "        conn.execute(select(key)).fetchone()\n"
        "        session.execute(select(key)).scalars().all()\n"
    )

    assert _hits(source) == [
        ("io_in_loop", 4, "conn.execute().fetchone"),
        ("io_in_loop", 5, "session.execute().scalars().all"),
    ]


def test_a_result_read_on_a_later_statement_is_still_its_own_hit():
    source = (
        "from sqlalchemy import select\n"
        "async def f(session, types):\n"
        "    for t in types:\n"
        "        result = await session.execute(select(Page.id))\n"
        "        rows = result.scalars().all()\n"
    )

    assert _hits(source) == [
        ("io_in_loop", 4, "session.execute"),
        ("serial_await_in_loop", 4, "session.execute"),
        ("io_in_loop", 5, "result.scalars().all"),
    ]


def test_an_await_on_an_outer_link_still_makes_the_query_a_serial_await():
    """The await sits on ``.fetchall()``; ``session.execute`` is the hit."""
    source = (
        "from sqlalchemy import select\n"
        "async def f(session, keys):\n"
        "    for key in keys:\n"
        "        rows = await (\n"
        "            session.execute(select(P).where(P.id == key))\n"
        "        ).fetchall()\n"
    )

    assert _hits(source) == [
        ("io_in_loop", 5, "session.execute"),
        ("serial_await_in_loop", 5, "session.execute"),
    ]


def test_the_loop_key_feeding_an_outer_link_still_counts():
    """``key`` reaches the query through ``.filter(...)``, a link above the
    innermost sink, on one line and over several."""
    one_line = (
        "from sqlalchemy import select\n"
        "def f(session, keys):\n"
        "    for key in keys:\n"
        "        row = session.execute(select(P)).filter(P.id == key).first()\n"
    )
    over_lines = (
        "from sqlalchemy import select\n"
        "def f(session, keys):\n"
        "    for key in keys:\n"
        "        row = (\n"
        "            session.execute(select(P))\n"
        "        ).filter(P.id == key).first()\n"
    )
    unused = (
        "from sqlalchemy import select\n"
        "def f(session, keys):\n"
        "    for key in keys:\n"
        "        row = (\n"
        "            session.execute(select(P))\n"
        "        ).filter(P.id == 1).first()\n"
    )

    def key_unused(source: str) -> list[bool]:
        return [bool(h.loop and h.loop.key_unused) for h in _walk(source).perf_hits]

    assert key_unused(one_line) == [False]
    assert key_unused(over_lines) == [False]
    assert key_unused(unused) == [True]


def test_two_sinks_passed_to_one_call_stay_two_hits():
    source = (
        "import requests\n"
        "def f(urls):\n"
        "    for u in urls:\n"
        "        merge(\n"
        "            requests.get(u),\n"
        "            requests.post(u),\n"
        "        )\n"
    )

    assert _hits(source) == [
        ("io_in_loop", 5, "requests.get"),
        ("io_in_loop", 6, "requests.post"),
    ]


def test_a_sink_in_an_argument_is_not_a_link_of_the_chain_it_is_passed_to():
    source = (
        "import requests\n"
        "from sqlalchemy import select\n"
        "def f(session, urls):\n"
        "    for u in urls:\n"
        "        session.execute(\n"
        "            build(requests.get(u))\n"
        "        ).all()\n"
    )

    assert _hits(source) == [
        ("io_in_loop", 5, "session.execute().all"),
        ("io_in_loop", 6, "requests.get"),
    ]


def test_a_receiver_that_is_a_choice_between_two_values_is_not_a_chain():
    """``.all()`` may run on ``pending``, which no inner call speaks for, so
    it stays a hit of its own beside the query in the other arm."""
    source = (
        "from sqlalchemy import select\n"
        "def f(session, types, pending):\n"
        "    for t in types:\n"
        "        rows = (\n"
        "            session.execute(select(P).where(P.t == t)) if t else pending\n"
        "        ).all()\n"
    )

    assert _hits(source) == [
        ("io_in_loop", 4, "all"),
        ("io_in_loop", 5, "session.execute"),
    ]


def test_a_nested_loop_reports_the_chain_once_per_marker():
    source = (
        "from sqlalchemy import select\n"
        "async def f(session, groups):\n"
        "    for group in groups:\n"
        "        for key in group:\n"
        "            row = (\n"
        "                await session.execute(select(P).where(P.id == key))\n"
        "            ).scalar_one_or_none()\n"
    )

    assert _hits(source) == [
        ("io_in_loop", 6, "session.execute"),
        ("nested_loop_with_io", 6, "session.execute"),
        ("serial_await_in_loop", 6, "session.execute"),
    ]


def test_outside_a_loop_the_function_s_facts_do_not_depend_on_the_line_breaks():
    one_line = (
        "from sqlalchemy import select\n"
        "async def f(session, t):\n"
        "    return (await session.execute(select(Page.id).where(Page.t == t))).scalars().all()\n"
    )
    over_lines = (
        "from sqlalchemy import select\n"
        "async def f(session, t):\n"
        "    return (\n"
        "        await session.execute(select(Page.id).where(Page.t == t))\n"
        "    ).scalars().all()\n"
    )

    def facts(source: str):
        """Every fact but the line numbers, which the line breaks do move."""
        return [
            {k: v for k, v in dataclasses.asdict(f).items() if "line" not in k}
            for f in _walk(source).perf_fn_facts
        ]

    assert _hits(one_line) == _hits(over_lines) == []
    assert facts(one_line) == facts(over_lines) != []
