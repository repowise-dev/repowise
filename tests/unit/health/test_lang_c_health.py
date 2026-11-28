"""Health metrics for C, pinned on functions from redis and libuv.

Each number below was counted by hand from the source: CCN is 1 plus one per
``if`` / loop / ``&&`` / ``||``, nesting is the deepest block, NLOC is the
non-blank, non-comment lines of the body (the signature line too when it holds
the opening brace). C has no classes, so there is nothing for god_class here.
"""

from __future__ import annotations

import pytest

from repowise.core.analysis.health.complexity import walk_file

# redis util.c digits10, object.c trimStringObjectIfNeeded, db.c setKey.
_REDIS = r"""uint32_t digits10(uint64_t v) {
    if (v < 10) return 1;
    if (v < 100) return 2;
    if (v < 1000) return 3;
    if (v < 1000000000000UL) {
        if (v < 100000000UL) {
            if (v < 1000000) {
                if (v < 10000) return 4;
                return 5 + (v >= 100000);
            }
            return 7 + (v >= 10000000UL);
        }
        if (v < 10000000000UL) {
            return 9 + (v >= 1000000000UL);
        }
        return 11 + (v >= 100000000000UL);
    }
    return 12 + digits10(v / 1000000000000UL);
}

void trimStringObjectIfNeeded(robj *o, int trim_small_values) {
    if (o->encoding != OBJ_ENCODING_RAW) return;
    /* A string may have free space when the query buffer was used directly,
     * or when the caller asks for small values to be trimmed. */
    size_t len = sdslen(o->ptr);
    if (len >= PROTO_MBULK_BIG_ARG ||
        trim_small_values ||
        (server.executing_client && server.executing_client->flags & CLIENT_SCRIPT && len < LUA_CMD_OBJCACHE_MAX_LEN)) {
        if (sdsavail(o->ptr) > len/10) {
            o->ptr = sdsRemoveFreeSpace(o->ptr, 0);
        }
    }
}

void setKey(client *c, redisDb *db, robj *key, robj **valref, int flags) {
    setKeyByLink(c, db, key, valref, flags, NULL);
}
"""

# libuv unix/core.c uv__backend_timeout, uv-common.c uv__count_bufs.
_LIBUV = r"""static int uv__backend_timeout(const uv_loop_t* loop) {
  if (loop->stop_flag == 0 &&
      /* uv__loop_alive(loop) && */
      (uv__has_active_handles(loop) || uv__has_active_reqs(loop)) &&
      uv__queue_empty(&loop->pending_queue) &&
      uv__queue_empty(&loop->idle_handles) &&
      (loop->flags & UV_LOOP_REAP_CHILDREN) == 0 &&
      loop->closing_handles == NULL)
    return uv__next_timeout(loop);
  return 0;
}

size_t uv__count_bufs(const uv_buf_t bufs[], unsigned int nbufs) {
  unsigned int i;
  size_t bytes;

  bytes = 0;
  for (i = 0; i < nbufs; i++) {
    if (bufs[i].len > (size_t) INT32_MAX - bytes)
      return INT32_MAX;
    bytes += bufs[i].len;
  }

  return bytes;
}
"""


def _walk(source: str):
    fcx = walk_file("src/sample.c", "c", source.encode())
    if not fcx.functions:
        pytest.skip("tree-sitter grammar for C unavailable")
    return fcx


def _metrics(source: str) -> dict[str, tuple[int, int, int, int]]:
    """``name -> (ccn, max_nesting, nloc, param_count)``."""
    return {f.name: (f.ccn, f.max_nesting, f.nloc, f.param_count) for f in _walk(source).functions}


def test_redis_functions() -> None:
    assert _metrics(_REDIS) == {
        # 8 ifs; the ladder under ``v < 1e12`` is four ifs deep.
        "digits10": (9, 4, 19, 1),
        # 3 ifs + ``|| || && &&`` (``&`` is bitwise); the 2 comment lines are not code.
        "trimStringObjectIfNeeded": (8, 2, 11, 2),
        # Five parameters, pointers included.
        "setKey": (1, 0, 3, 5),
    }


def test_libuv_functions() -> None:
    assert _metrics(_LIBUV) == {
        # 1 if + 5 ``&&`` + 1 ``||``; the commented-out operand is not counted.
        "uv__backend_timeout": (8, 1, 10, 1),
        "uv__count_bufs": (3, 2, 11, 2),
    }


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        pytest.param(_REDIS, [("trimStringObjectIfNeeded", 26, 4)], id="redis"),
        pytest.param(_LIBUV, [("uv__backend_timeout", 2, 6)], id="libuv"),
    ],
)
def test_compound_conditions(source: str, expected: list[tuple[str, int, int]]) -> None:
    """``(function, line, operators)`` of each condition complex_conditional reads.

    Both mix ``&&`` and ``||``, so neither is a flat list of alternatives.
    """
    conditions = [(f.name, c) for f in _walk(source).functions for c in f.complex_conditions]
    found = [(name, c.line, c.operator_count) for name, c in conditions if c.operator_count >= 3]
    assert found == expected
    assert not any(c.flat_chain for _, c in conditions if c.operator_count >= 3)
