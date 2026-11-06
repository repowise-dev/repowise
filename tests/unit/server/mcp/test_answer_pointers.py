"""get_answer locate pointers: line ranges on quoted code, get_symbol calls for named helpers."""

from __future__ import annotations

from types import SimpleNamespace

from repowise.server.mcp_server.tool_answer.pointers import (
    anchor_code_blocks,
    chain_first,
    helper_calls,
)

SOURCE = """export const compose = (middleware) => {
  return (context, next) => {
    let index = -1
    return dispatch(0)

    async function dispatch(i) {
      if (i <= index) {
        throw new Error('next() called multiple times')
      }
      index = i
      let res
      return context
    }
  }
}
""".splitlines()


def _reader(files: dict[str, list[str]]):
    return lambda path: files.get(path)


def test_cited_block_gets_its_line_range():
    answer = (
        "It checks:\n\n```ts\nif (i <= index) {\n  throw new Error('next() called multiple times')\n"
        "}\nindex = i\n```\n\n(src/compose.ts)\n"
    )
    out = anchor_code_blocks(answer, ["src/compose.ts"], _reader({"src/compose.ts": SOURCE}))
    assert "(src/compose.ts:7-10)" in out


def test_backticked_citation_and_elided_lines_still_anchor():
    answer = "```ts\nif (i <= index) {\n  ...\nindex = i\n```\n(`src/compose.ts`, `dispatch`)"
    out = anchor_code_blocks(answer, ["src/compose.ts"], _reader({"src/compose.ts": SOURCE}))
    assert "(`src/compose.ts:7-10`, `dispatch`)" in out


def test_uncited_block_gets_a_citation_only_when_one_file_holds_it():
    answer = "Logic:\n\n```ts\nlet index = -1\nreturn dispatch(0)\n```\n\nThen more."
    one = anchor_code_blocks(answer, ["src/compose.ts"], _reader({"src/compose.ts": SOURCE}))
    assert "```\n(src/compose.ts:3-4)\n\nThen more." in one
    both = anchor_code_blocks(
        answer, ["src/compose.ts", "src/copy.ts"],
        _reader({"src/compose.ts": SOURCE, "src/copy.ts": SOURCE}),
    )
    assert both == answer


def test_block_not_in_the_source_leaves_the_answer_unchanged():
    answer = "```ts\nconst nothing = here\n```\n(src/compose.ts)"
    assert anchor_code_blocks(answer, ["src/compose.ts"], _reader({"src/compose.ts": SOURCE})) == answer


def _row(name, parent, path="httpx/_client.py"):
    sid = f"{path}::{parent}::{name}" if parent else f"{path}::{name}"
    return SimpleNamespace(name=name, parent_name=parent, file_path=path, symbol_id=sid)


def test_helper_calls_pick_the_class_the_answer_names_and_skip_served_bodies():
    rows = [
        _row("_send_single_request", "Client"),
        _row("_send_single_request", "AsyncClient"),
        _row("send", "Client"),
        _row("send", "AsyncClient"),
        _row("__init__", "Client"),
    ]
    answer = "`Client.__init__` stores defaults; `send()` calls `_send_single_request` on `Client`."
    calls = helper_calls(answer, rows, served={("httpx/_client.py", "__init__")})
    assert calls == [
        "get_symbol('httpx/_client.py::Client::send')",
        "get_symbol('httpx/_client.py::Client::_send_single_request')",
    ]


def test_helper_calls_skip_a_name_the_answer_does_not_settle_and_cap_at_three():
    rows = [_row("run_step", "A"), _row("run_step", "B")] + [_row(f"f{n}", None) for n in range(5)]
    answer = "`run_step` then `f0`, `f1`, `f2`, `f3`."
    assert helper_calls(answer, rows, served=set()) == [
        "get_symbol('httpx/_client.py::f0')",
        "get_symbol('httpx/_client.py::f1')",
        "get_symbol('httpx/_client.py::f2')",
    ]


def test_a_plain_word_counts_only_when_written_as_a_call():
    rows = [_row("scheme", "URL"), _row("enforce_budget", None)]
    answer = "Fields include `scheme`; `enforce_budget(...)` trims the reply."
    assert helper_calls(answer, rows, served=set()) == [
        "get_symbol('httpx/_client.py::enforce_budget')"
    ]
    assert helper_calls("It calls `scheme()`.", rows, served=set()) == [
        "get_symbol('httpx/_client.py::URL::scheme')"
    ]


def test_a_short_or_repeated_block_is_not_anchored():
    reader = _reader({"src/compose.ts": SOURCE})
    short = "```ts\n}\n```\n(src/compose.ts)"
    assert anchor_code_blocks(short, ["src/compose.ts"], reader) == short
    twice = SOURCE + SOURCE
    answer = "```ts\nif (i <= index) {\n```\n(src/compose.ts)"
    assert anchor_code_blocks(answer, ["src/compose.ts"], _reader({"src/compose.ts": twice})) == answer


def test_names_inside_quoted_code_are_not_helpers():
    rows = [_row("render_row", None)]
    answer = "It builds rows:\n\n```js\nconst s = `render_row()`\n```\n"
    assert helper_calls(answer, rows, served=set()) == []


def test_a_call_chain_answer_reads_the_first_helper_with_its_callees():
    calls = ["get_symbol('command.go::Command::ExecuteC')", "get_symbol('command.go::Command::execute')"]
    assert chain_first(calls) == [
        "get_symbol('command.go::Command::ExecuteC', depth=2)",
        "get_symbol('command.go::Command::execute')",
    ]
    assert chain_first([]) == []


def test_the_chain_entry_is_the_helper_the_question_names():
    calls = [
        "get_symbol('src/hono-base.ts::Hono::#handleError')",
        "get_symbol('src/hono-base.ts::Hono::fetch')",
        "get_symbol('src/utils/url.ts::getPath')",
    ]
    assert chain_first(calls, "What happens inside `app.fetch(request)`?") == [
        "get_symbol('src/hono-base.ts::Hono::fetch', depth=2)",
        "get_symbol('src/hono-base.ts::Hono::#handleError')",
        "get_symbol('src/utils/url.ts::getPath')",
    ]
