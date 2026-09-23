"""Unit tests for the TS/JS promotion-fact hooks (``loop_magnitude`` /
``batch_form`` / ``is_chunked_loop``) on ``TsJsPerfDialect``.

Goes through the real walker (``walk_file``) rather than calling the dialect
methods directly, so the tests also cover the sink-detection gate each hook
depends on (a hit only carries ``.loop`` facts when its call is first
classified as an I/O sink). Precision over recall: every ambiguous case
asserts ``None`` / ``"unknown"``, not a guess.
"""

from __future__ import annotations

import pytest

from repowise.core.analysis.health.complexity import walk_file


def _hits(src: str, kind: str | None = None):
    fc = walk_file("f.ts", "typescript", src.encode())
    hits = fc.perf_hits
    if kind is not None:
        hits = [h for h in hits if h.kind == kind]
    return hits


def _io_hits(src: str):
    return _hits(src, "io_in_loop")


# ---------------------------------------------------------------------------
# batch_form — Prisma
# ---------------------------------------------------------------------------


def test_prisma_find_unique_batches_equivalent():
    src = """
async function f(users) {
  for (const u of users) {
    await prisma.user.findUnique({ where: { id: u.id } });
  }
}
"""
    hits = _io_hits(src)
    loop = hits[0].loop
    assert loop is not None and loop.batch is not None
    assert loop.batch.call == "prisma.user.findMany({ where: { id: { in: keys } } })"
    assert loop.batch.equivalent is True


def test_prisma_find_first_not_equivalent():
    src = """
async function f(users) {
  for (const u of users) {
    await prisma.user.findFirst({ where: { id: u.id } });
  }
}
"""
    hits = _io_hits(src)
    loop = hits[0].loop
    assert loop is not None and loop.batch is not None
    assert loop.batch.call == "prisma.user.findMany({ where: { id: { in: keys } } })"
    assert loop.batch.equivalent is False


@pytest.mark.parametrize(
    "body",
    [
        # The element is a bystander; the filter value is a constant.
        "await prisma.user.findUnique({ where: { id: ROOT_ID }, include: { x: u.flag } });",
        # The element feeds two arguments, so no single key column exists.
        "await prisma.user.findUnique({ where: { id: u.id }, select: { [u.col]: true } });",
    ],
)
def test_no_batch_form_unless_the_element_is_the_filter_value(body):
    src = f"async function f(users) {{ for (const u of users) {{ {body} }} }}"
    assert all(h.loop is None or h.loop.batch is None for h in _io_hits(src))


def test_no_batch_form_for_a_cursor_loop_or_a_mutated_iterable():
    cursor = """
async function f(users) {
  for (let i = 0; i < users.length; i++) {
    await prisma.user.findUnique({ where: { id: users[i].id } });
  }
}
"""
    worklist = """
async function f(users) {
  for (const u of users) {
    await prisma.user.findUnique({ where: { id: u.id } });
    users.push(u);
  }
}
"""
    for src in (cursor, worklist):
        assert all(h.loop is None or h.loop.batch is None for h in _io_hits(src))


def test_prisma_delete_batches_equivalent():
    # Bare ``delete`` (unlike ``deleteMany``) is not a distinctive-enough verb
    # to trust without import resolution (it collides with Map/Set/cache
    # ``.delete()``), so this fixture imports the client the same way
    # ``collect_io_names`` resolves any other db client — the sink then fires
    # via the ``awaited`` arm of ``sink_kind`` regardless of the method name.
    src = """
import { prisma } from "@prisma/client";
async function f(users) {
  for (const u of users) {
    await prisma.user.delete({ where: { id: u.id } });
  }
}
"""
    hits = _io_hits(src)
    loop = hits[0].loop
    assert loop is not None and loop.batch is not None
    assert loop.batch.call == "prisma.user.deleteMany({ where: { id: { in: keys } } })"
    assert loop.batch.equivalent is True


def test_prisma_delete_with_break_not_equivalent():
    src = """
import { prisma } from "@prisma/client";
async function f(users) {
  for (const u of users) {
    if (!u.id) { break; }
    await prisma.user.delete({ where: { id: u.id } });
  }
}
"""
    hits = _io_hits(src)
    loop = hits[0].loop
    assert loop is not None and loop.batch is not None
    assert loop.batch.equivalent is False


# ---------------------------------------------------------------------------
# loop_magnitude
# ---------------------------------------------------------------------------


def test_magnitude_grows_with_prisma_iterable():
    src = """
async function f() {
  const users = await prisma.user.findMany();
  for (const u of users) {
    await log(u.id);
    await prisma.post.deleteMany({ where: { userId: u.id } });
  }
}
"""
    hits = _io_hits(src)
    kinds = {h.detail for h in hits}
    assert "db" in kinds
    hit = next(h for h in hits if h.detail == "db")
    assert hit.loop is not None
    assert hit.loop.magnitude == "grows_with_data"


def test_magnitude_grows_with_fetch_json_one_hop():
    src = """
async function f() {
  const rows = (await fetch(url)).json();
  for (const r of rows) {
    await prisma.post.deleteMany({ where: { userId: r.id } });
  }
}
"""
    hits = _io_hits(src)
    assert hits, "expected an io_in_loop hit"
    assert hits[0].loop is not None
    assert hits[0].loop.magnitude == "grows_with_data"


def test_a_directory_listing_is_as_unknown_as_the_path_it_is_given():
    src = """
function f(dir) {
  const files = fs.readdirSync(dir);
  for (const name of files) {
    prisma.file.deleteMany({ where: { name: name } });
  }
}
"""
    hits = _io_hits(src)
    assert hits[0].loop is None or hits[0].loop.magnitude == "unknown"


def test_magnitude_bounded_via_slice():
    src = """
async function f(items, LIMIT) {
  for (const x of items.slice(0, 5)) {
    await prisma.post.deleteMany({ where: { userId: x.id } });
  }
}
"""
    hits = _io_hits(src)
    assert hits[0].loop is not None
    assert hits[0].loop.magnitude == "bounded"


def test_magnitude_unknown_for_parameter():
    src = """
async function f(items) {
  for (const x of items) {
    await prisma.post.deleteMany({ where: { userId: x.id } });
  }
}
"""
    hits = _io_hits(src)
    loop = hits[0].loop
    # ``items`` is a bare parameter — no grows/bounded proof, so either no
    # loop facts at all or an explicit "unknown" magnitude.
    assert loop is None or loop.magnitude == "unknown"


# ---------------------------------------------------------------------------
# is_chunked_loop
# ---------------------------------------------------------------------------


def test_chunked_c_style_step():
    src = """
function f(ids, n) {
  for (let i = 0; i < n; i += 50) {
    prisma.user.deleteMany({ where: { id: { in: ids.slice(i, i + 50) } } });
  }
}
"""
    hits = _io_hits(src)
    assert hits
    assert hits[0].loop is not None
    assert hits[0].loop.chunked is True


def test_chunked_lodash_chunk_helper():
    src = """
async function f(ids) {
  for (const batch of _.chunk(ids, 50)) {
    await prisma.user.deleteMany({ where: { id: { in: batch } } });
  }
}
"""
    hits = _io_hits(src)
    assert hits
    assert hits[0].loop is not None
    assert hits[0].loop.chunked is True


def test_not_chunked_step_one():
    src = """
function f(ids, n) {
  for (let i = 0; i < n; i += 1) {
    prisma.user.deleteMany({ where: { id: ids[i] } });
  }
}
"""
    hits = _io_hits(src)
    assert hits
    assert hits[0].loop is None or hits[0].loop.chunked is False


# ---------------------------------------------------------------------------
# runs_in_place / concurrency_bound: an awaited limiter closure is the loop body
# ---------------------------------------------------------------------------


def test_stored_closure_is_not_in_place():
    src = """
async function f(xs) {
  for (const x of xs) {
    const cb = () => fetch(x);
    await limit(cb);
  }
}
"""
    assert _io_hits(src) == []


def test_returned_closure_is_not_in_place():
    src = """
function make(xs) {
  for (const x of xs) {
    return () => fetch(x);
  }
}
"""
    assert _io_hits(src) == []


def test_unawaited_limiter_call_is_not_in_place():
    # The fan-out already happens here: `limit(...)` is queued, not awaited.
    src = """
async function f(xs) {
  for (const x of xs) {
    tasks.push(limit(() => fetch(x)));
  }
}
"""
    assert _io_hits(src) == []


def test_limiter_call_inside_a_non_limiter_callback_is_not_in_place():
    # The outer closure is the argument of `other(...)`, not a limiter — its
    # boundary resets loop_depth regardless of the limiter call nested inside.
    src = """
async function f(xs) {
  for (const x of xs) {
    await other(() => { limit(() => fetch(x)); });
  }
}
"""
    assert _io_hits(src) == []


def test_closure_passed_to_settimeout_is_not_in_place():
    src = """
async function f(xs) {
  for (const x of xs) {
    setTimeout(() => fetch(x), 0);
  }
}
"""
    assert _io_hits(src) == []


def test_limiter_built_inside_the_loop_body_bounds_nothing():
    # `pLimit(2)` is itself a call, so the enclosing call's callee is a
    # `call_expression`, not an identifier/member naming a limiter — the
    # closure does not run in place, and even if it did there would be no
    # stable limiter identity to report.
    src = """
async function f(xs) {
  for (const x of xs) {
    await pLimit(2)(() => fetch(x));
  }
}
"""
    assert _io_hits(src) == []


def test_break_in_body_gives_a_hit_with_no_bound():
    src = """
async function f(xs) {
  for (const x of xs) {
    if (!x) break;
    await limit(() => fetch(x));
  }
}
"""
    hits = _io_hits(src)
    assert hits
    assert hits[0].loop is None or hits[0].loop.concurrency_bound is None


def test_limit_closure_runs_in_place_and_bounds_the_sink():
    src = """
async function f(xs) {
  for (const x of xs) {
    await limit(() => fetch(x));
  }
}
"""
    for kind in ("io_in_loop", "serial_await_in_loop"):
        hits = _hits(src, kind)
        assert hits and hits[0].loop is not None
        assert hits[0].loop.concurrency_bound == "limit"


def test_mutex_run_exclusive_closure_runs_in_place_and_bounds_the_sink():
    src = """
async function f(xs) {
  for (const x of xs) {
    await mutex.runExclusive(async () => { await fetch(x); });
  }
}
"""
    hits = _io_hits(src)
    assert hits
    assert hits[0].loop is not None
    assert hits[0].loop.concurrency_bound == "mutex.runExclusive"
    # The sink is awaited inside the closure too, so it is additionally a
    # missed-concurrency co-signal — carrying the same bound.
    serial = _hits(src, "serial_await_in_loop")
    assert serial
    assert serial[0].loop is not None
    assert serial[0].loop.concurrency_bound == "mutex.runExclusive"


def test_prisma_find_unique_pushed_in_key_order_not_equivalent():
    src = """
async function f(users) {
  const out = [];
  for (const u of users) {
    out.push(await prisma.user.findUnique({ where: { id: u.id } }));
  }
  return out;
}
"""
    loop = _io_hits(src)[0].loop
    assert loop is not None and loop.batch is not None and loop.batch.equivalent is False


def test_a_float_step_of_one_is_not_chunked():
    src = """
async function f(ids) {
  for (let i = 0; i < ids.length; i += 1.0) {
    await fetch(ids[i]);
  }
}
"""
    loop = _io_hits(src)[0].loop
    assert loop is None or not loop.chunked


def test_a_push_inside_a_limiter_closure_still_breaks_equivalence():
    src = """
async function f(users) {
  const out = [];
  for (const u of users) {
    await mutex.runExclusive(async () => {
      out.push(await prisma.user.findUnique({ where: { id: u.id } }));
    });
  }
  return out;
}
"""
    loop = _io_hits(src)[0].loop
    assert loop is not None and loop.batch is not None and loop.batch.equivalent is False


def test_an_awaited_non_limiter_callback_is_not_in_place():
    src = """
async function f(xs) {
  for (const x of xs) {
    await retry(() => fetch(x));
  }
}
"""
    assert _io_hits(src) == []
