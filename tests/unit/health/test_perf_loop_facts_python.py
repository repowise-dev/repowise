"""Unit tests for the Python promotion-facts hooks (perf/dialects/python.py).

Covers ``loop_magnitude`` / ``batch_form`` / ``concurrency_bound`` through the
real walker (``walk_file`` -> ``FileComplexity.perf_hits[i].loop``), mirroring
``test_perf_io_in_loop.py``'s inline-fixture style. Every positive rule is
paired with the refusal(s) that protect precision (SPEC.md "Dialect hooks").
"""

from __future__ import annotations

from repowise.core.analysis.health.complexity import walk_file
from repowise.core.analysis.health.perf.loop_facts import LoopFacts


def _hits(src: bytes, kind: str = "io_in_loop") -> list:
    fc = walk_file("f.py", "python", src)
    return [h for h in fc.perf_hits if h.kind == kind]


def _loop(src: bytes, kind: str = "io_in_loop", index: int = 0) -> LoopFacts | None:
    hits = _hits(src, kind)
    assert hits, f"no {kind!r} hit produced"
    return hits[index].loop


# ---------------------------------------------------------------------------
# loop_magnitude
# ---------------------------------------------------------------------------


def test_magnitude_grows_via_lexical_reaching_assignment():
    """``rows = await session.execute(...)`` then ``for r in rows`` -> grows (G1)."""
    facts = _loop(
        b"from sqlalchemy import select\n"
        b"async def f(session):\n"
        b"    rows = await session.execute(select(1))\n"
        b"    for r in rows:\n"
        b"        await session.execute(select(r))\n"
    )
    assert facts.magnitude == "grows_with_data"


def test_magnitude_grows_via_projection_chain():
    """The iterable itself is a ``.scalars().all()`` projection of a sink call."""
    facts = _loop(
        b"from sqlalchemy import select\n"
        b"async def f(session):\n"
        b"    for r in session.execute(select(1)).scalars().all():\n"
        b"        await session.execute(select(r))\n"
    )
    assert facts.magnitude == "grows_with_data"


def test_magnitude_range_len_of_grown_source_grows():
    """``range(len(rows))`` resolved through ``rows`` the same way as G1."""
    facts = _loop(
        b"from sqlalchemy import select\n"
        b"async def f(session):\n"
        b"    rows = await session.execute(select(1))\n"
        b"    for i in range(len(rows)):\n"
        b"        await session.execute(select(rows))\n"
    )
    assert facts.magnitude == "grows_with_data"


def test_magnitude_os_walk_grows():
    """G2: a filesystem listing call is data-dependent."""
    facts = _loop(
        b"import os\n"
        b"from sqlalchemy import select\n"
        b"async def f(session):\n"
        b"    for root_, dirs, files in os.walk('/tmp'):\n"
        b"        await session.execute(select(root_))\n"
    )
    assert facts.magnitude == "grows_with_data"


def test_magnitude_range_all_caps_constant_bounded():
    """B1: ``range(N)`` where N is an ALL_CAPS identifier."""
    facts = _loop(
        b"from sqlalchemy import select\n"
        b"async def f(session, MAX_RETRIES):\n"
        b"    for i in range(MAX_RETRIES):\n"
        b"        await session.execute(select(i))\n"
    )
    assert facts.magnitude == "bounded"


def test_magnitude_range_attempts_name_bounded():
    """B1: the last dotted segment matches ``retr|attempt`` (case-insensitive)."""
    facts = _loop(
        b"from sqlalchemy import select\n"
        b"async def f(session, cfg):\n"
        b"    for i in range(cfg.max_attempts):\n"
        b"        await session.execute(select(i))\n"
    )
    assert facts.magnitude == "bounded"


def test_magnitude_slice_constant_upper_bound_bounded():
    """B1: ``xs[:5]``."""
    facts = _loop(
        b"from sqlalchemy import select\n"
        b"async def f(session, xs):\n"
        b"    for x in xs[:5]:\n"
        b"        await session.execute(select(x))\n"
    )
    assert facts.magnitude == "bounded"


def test_magnitude_slice_named_constant_bounded():
    """B1: ``xs[:LIMIT]``."""
    facts = _loop(
        b"from sqlalchemy import select\n"
        b"async def f(session, xs, LIMIT):\n"
        b"    for x in xs[:LIMIT]:\n"
        b"        await session.execute(select(x))\n"
    )
    assert facts.magnitude == "bounded"


def test_magnitude_slice_variable_width_int_literal_bounded():
    """B1: ``xs[a:a+4]`` — same start name, integer-literal width."""
    facts = _loop(
        b"from sqlalchemy import select\n"
        b"async def f(session, xs, a):\n"
        b"    for x in xs[a:a+4]:\n"
        b"        await session.execute(select(x))\n"
    )
    assert facts.magnitude == "bounded"


def test_magnitude_parameter_iterable_is_unknown():
    """Parameters are unknown on purpose — no reaching assignment to resolve."""
    facts = _loop(
        b"from sqlalchemy import select\n"
        b"async def f(session, repos):\n"
        b"    for r in repos:\n"
        b"        await session.execute(select(r))\n"
    )
    assert facts is None  # unknown magnitude + no batch/bound -> no LoopFacts at all


# ---------------------------------------------------------------------------
# batch_form
# ---------------------------------------------------------------------------


def test_batch_supabase_select_eq_equivalent():
    facts = _loop(
        b"async def f(client, repos):\n"
        b"    for r in repos:\n"
        b"        await client.table('t').select('*').eq('repo_id', r.id).execute()\n"
    )
    assert facts.batch.call == '.in_("repo_id", keys)'
    assert facts.batch.equivalent is True


def test_batch_supabase_delete_eq_equivalent():
    facts = _loop(
        b"async def f(client, repos):\n"
        b"    for r in repos:\n"
        b"        await client.table('t').delete().eq('repo_id', r.id).execute()\n"
    )
    assert facts.batch.call == '.in_("repo_id", keys)'
    assert facts.batch.equivalent is True


def test_batch_sqlalchemy_execute_where_equality():
    facts = _loop(
        b"from sqlalchemy import select\n"
        b"async def f(session, ids):\n"
        b"    for i in ids:\n"
        b"        await session.execute(select(Model).where(Model.id == i))\n"
    )
    assert facts.batch.call == "Model.id.in_(keys)"
    assert facts.batch.equivalent is True


def test_batch_sqlalchemy_query_filter_first_not_equivalent():
    """``.first()`` is itself a per-key limiting verb -> form returned, not equivalent."""
    facts = _loop(
        b"from sqlalchemy import select\n"
        b"def f(session, ids):\n"
        b"    for i in ids:\n"
        b"        session.query(Model).filter(Model.id == i).first()\n"
    )
    assert facts.batch.call == "Model.id.in_(keys)"
    assert facts.batch.equivalent is False


# -- refusals ----------------------------------------------------------------


def test_batch_none_when_loop_var_used_twice_in_chain():
    """Two ``.eq()`` calls each reference the loop var -> not a single-key chain."""
    facts = _loop(
        b"async def f(client, repos):\n"
        b"    for r in repos:\n"
        b"        await client.table('t').select('*')"
        b".eq('repo_id', r.id).eq('name', r.name).execute()\n"
    )
    assert facts is None


def test_batch_supabase_limit_makes_not_equivalent():
    facts = _loop(
        b"async def f(client, repos):\n"
        b"    for r in repos:\n"
        b"        await client.table('t').select('*')"
        b".eq('repo_id', r.id).limit(1).execute()\n"
    )
    assert facts.batch.equivalent is False


def test_batch_supabase_single_makes_not_equivalent():
    facts = _loop(
        b"async def f(client, repos):\n"
        b"    for r in repos:\n"
        b"        await client.table('t').select('*')"
        b".eq('repo_id', r.id).single().execute()\n"
    )
    assert facts.batch.equivalent is False


def test_batch_second_sink_in_body_makes_not_equivalent():
    hits = _hits(
        b"from sqlalchemy import select\n"
        b"async def f(client, repos, session):\n"
        b"    for r in repos:\n"
        b"        session.execute(select(r))\n"
        b"        await client.table('t').select('*').eq('repo_id', r.id).execute()\n"
    )
    supabase_hit = next(h for h in hits if h.loop is not None)
    assert supabase_hit.loop.batch.equivalent is False


def test_batch_delete_with_break_not_equivalent():
    """An early exit could skip work the batched delete would still do."""
    facts = _loop(
        b"async def f(client, repos):\n"
        b"    for r in repos:\n"
        b"        if r.id == 0:\n"
        b"            break\n"
        b"        await client.table('t').delete().eq('repo_id', r.id).execute()\n"
    )
    assert facts.batch.call == '.in_("repo_id", keys)'
    assert facts.batch.equivalent is False


def test_batch_none_when_iterable_appended_in_body():
    facts = _loop(
        b"async def f(client, repos):\n"
        b"    for r in repos:\n"
        b"        repos.append(r)\n"
        b"        await client.table('t').select('*').eq('repo_id', r.id).execute()\n"
    )
    assert facts is None


def test_batch_none_for_while_loop():
    facts = _loop(
        b"async def f(client, repos):\n"
        b"    r = repos.pop()\n"
        b"    while r is not None:\n"
        b"        await client.table('t').select('*').eq('repo_id', r.id).execute()\n"
        b"        r = repos.pop() if repos else None\n"
    )
    assert facts is None


# ---------------------------------------------------------------------------
# concurrency_bound
# ---------------------------------------------------------------------------


def test_concurrency_bound_async_with_self_attribute():
    facts = _loop(
        b"from sqlalchemy import select\n"
        b"async def f(self, items, session):\n"
        b"    for x in items:\n"
        b"        async with self._sem:\n"
        b"            await session.execute(select(x))\n"
    )
    assert facts.concurrency_bound == "self._sem"


def test_a_semaphore_built_inside_the_loop_bounds_nothing():
    facts = _loop(
        b"import asyncio\n"
        b"from sqlalchemy import select\n"
        b"async def f(items, session):\n"
        b"    for x in items:\n"
        b"        async with asyncio.Semaphore(5):\n"
        b"            await session.execute(select(x))\n"
    )
    assert facts is None or facts.concurrency_bound is None


def test_concurrency_bound_none_when_loop_has_break():
    """Spec amendment: an early-exit loop refuses concurrency_bound — fan-out
    under the semaphore would run work the sequential loop skips past the exit."""
    facts = _loop(
        b"from sqlalchemy import select\n"
        b"async def f(self, items, session):\n"
        b"    for x in items:\n"
        b"        if x.bad:\n"
        b"            break\n"
        b"        async with self._sem:\n"
        b"            await session.execute(select(x))\n"
    )
    assert facts is None


def test_concurrency_bound_none_when_loop_has_return():
    facts = _loop(
        b"from sqlalchemy import select\n"
        b"async def f(self, items, session):\n"
        b"    for x in items:\n"
        b"        if x.bad:\n"
        b"            return None\n"
        b"        async with self._sem:\n"
        b"            await session.execute(select(x))\n"
    )
    assert facts is None


def test_concurrency_bound_none_for_sync_with():
    """Only an ``async with`` counts — a plain ``with`` never yields to other tasks."""
    facts = _loop(
        b"from sqlalchemy import select\n"
        b"async def f(self, items, session):\n"
        b"    for x in items:\n"
        b"        with self._sem:\n"
        b"            await session.execute(select(x))\n"
    )
    assert facts is None


def test_batch_none_when_the_key_is_computed_from_the_element():
    facts = _loop(
        b"async def f(client, repos):\n"
        b"    for r in repos:\n"
        b"        await client.table('t').select('*').eq('id', slug(r)).execute()\n"
    )
    assert facts is None or facts.batch is None


def test_a_sync_network_write_in_the_body_breaks_equivalence():
    facts = _loop(
        b"import requests\n"
        b"def f(client, repos):\n"
        b"    for r in repos:\n"
        b"        client.table('t').select('*').eq('id', r.id).execute()\n"
        b"        requests.post('https://x', json={})\n"
    )
    assert facts.batch is not None and facts.batch.equivalent is False


def test_magnitude_grows_through_a_result_attribute():
    facts = _loop(
        b"def f(client):\n"
        b"    res = client.table('repos').select('*').execute()\n"
        b"    for row in res.data:\n"
        b"        client.table('t').select('*').eq('id', row['id']).execute()\n"
    )
    assert facts.magnitude == "grows_with_data"


def test_a_retry_loop_inside_a_loop_over_rows_is_not_bounded():
    facts = _loop(
        b"async def f(session):\n"
        b"    rows = (await session.execute(q)).scalars().all()\n"
        b"    for row in rows:\n"
        b"        for attempt in range(MAX_RETRIES):\n"
        b"            await session.execute(select(row))\n"
    )
    assert facts.magnitude == "grows_with_data"


def test_a_delete_behind_a_continue_is_not_equivalent():
    """Collected up front, the keys would include the records the loop skips."""
    facts = _loop(
        b"from sqlalchemy import delete\n"
        b"async def f(session, rows):\n"
        b"    for rec in rows:\n"
        b"        if not rec.files:\n"
        b"            continue\n"
        b"        await session.execute(delete(Link).where(Link.decision_id == rec.id))\n"
    )
    assert facts.batch is not None and facts.batch.equivalent is False


def test_a_result_cut_to_one_row_after_the_call_is_not_equivalent():
    facts = _loop(
        b"from sqlalchemy import select\n"
        b"async def f(session, targets):\n"
        b"    for t in targets:\n"
        b"        res = await session.execute(select(Meta).where(Meta.path == t))\n"
        b"        meta = res.scalar_one_or_none()\n"
    )
    assert facts.batch is not None and facts.batch.equivalent is False


def test_no_batch_form_when_the_filter_also_reads_a_loop_local():
    """The key is (src, kind), not the element: IN on one column is the wrong query."""
    facts = _loop(
        b"from sqlalchemy import select\n"
        b"async def f(session, edges):\n"
        b"    for edge in edges:\n"
        b"        src = remap(edge)\n"
        b"        await session.execute(select(E).where(E.src == src, E.kind == edge.kind))\n"
    )
    assert facts is None or facts.batch is None


def test_a_parenthesised_await_is_still_the_sinks_own():
    facts = _loop(
        b"async def f(supabase, payload, installation_id):\n"
        b"    for repo in payload.get('repositories_removed', []):\n"
        b"        await (\n"
        b"            supabase.table('app_repos').delete()\n"
        b"            .eq('installation_id', installation_id).eq('repo_id', repo['id']).execute()\n"
        b"        )\n"
    )
    assert facts.batch is not None
    assert facts.batch.call == '.in_("repo_id", keys)'
    assert facts.batch.equivalent is True


def test_writing_through_the_element_does_not_make_it_loop_local():
    facts = _loop(
        b"from sqlalchemy import select\n"
        b"async def f(session, targets, out):\n"
        b"    for t in targets:\n"
        b"        res = await session.execute(select(Meta).where(Meta.path == t))\n"
        b"        out[t] = res\n"
    )
    assert facts.batch is not None and facts.batch.call == "Meta.path.in_(keys)"


def test_magnitude_sees_through_an_empty_fallback():
    facts = _loop(
        b"def f(sb):\n"
        b"    rows = (sb.table('repos').select('*').execute()).data or []\n"
        b"    for row in rows:\n"
        b"        sb.table('t').select('*').eq('id', row['id']).execute()\n"
    )
    assert facts.magnitude == "grows_with_data"
