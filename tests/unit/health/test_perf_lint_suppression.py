"""An ESLint ``no-await-in-loop`` suppression states that awaiting in that loop is intended.

ky ``Ky.#run`` retries a request in a loop, each retry awaited under
``// eslint-disable-next-line no-await-in-loop``; the loop was reported as
I/O per iteration through ``#retryFromError -> #retry -> #fetch``.
"""

from __future__ import annotations

from repowise.core.analysis.health.complexity import walk_file
from repowise.core.analysis.health.perf.dialects.ts_js import _eslint_allowed_lines

_RULE = "no-await-in-loop"


def _hits(src: str) -> list[tuple[str, int]]:
    return sorted((h.kind, h.line) for h in walk_file("t.ts", "typescript", src.encode()).perf_hits)


def test_directive_lines():
    src = (
        b"a\n"
        b"// eslint-disable-next-line no-await-in-loop -- retries are sequential\n"
        b"c\n"
        b"d // eslint-disable-line no-await-in-loop\n"
        b"/* eslint-disable no-await-in-loop */\n"
        b"f\n"
        b"/* eslint-enable no-await-in-loop */\n"
        b"h\n"
        b"// eslint-disable-next-line no-console\n"
        b"j\n"
        b"// eslint-disable-next-line\n"
        b"l\n"
        b"// eslint-disable-next-line no-console, no-await-in-loop\n"
        b"n\n"
    )
    assert sorted(_eslint_allowed_lines(src, _RULE)) == [3, 4, 5, 6, 7, 14]


def test_unclosed_disable_runs_to_the_end():
    assert sorted(_eslint_allowed_lines(b"a\n/* eslint-disable no-await-in-loop */\nc\nd", _RULE)) == [
        2,
        3,
        4,
    ]


_LOOP = (
    "import axios from 'axios'\n"
    "async function f(urls, paths){\n"
    "  for (const u of urls){\n"
    "    {directive}\n"
    "    await axios.get(u)\n"
    "  }\n"
    "}\n"
)


def test_suppressed_await_is_not_a_finding():
    src = _LOOP.replace("{directive}", "// eslint-disable-next-line no-await-in-loop")
    assert _hits(src) == []


def test_other_rules_and_blanket_suppressions_do_not_count():
    for directive in ("// eslint-disable-next-line no-console", "// eslint-disable-next-line"):
        src = _LOOP.replace("{directive}", directive)
        assert _hits(src) == [("io_in_loop", 5), ("serial_await_in_loop", 5)], directive


def test_suppression_covers_only_its_line():
    src = (
        "import axios from 'axios'\n"
        "async function f(urls){\n"
        "  for (const u of urls){\n"
        "    // eslint-disable-next-line no-await-in-loop\n"
        "    await axios.get(u)\n"
        "    await axios.post(u)\n"
        "  }\n"
        "}\n"
    )
    assert _hits(src) == [("io_in_loop", 6), ("serial_await_in_loop", 6)]


def test_suppressed_awaited_helper_is_not_a_loop_call_target():
    src = (
        "class Ky {\n"
        "  async run(){\n"
        "    for (;;){\n"
        "      // eslint-disable-next-line no-await-in-loop\n"
        "      response = await this.retryFromError(error)\n"
        "      log(response)\n"
        "    }\n"
        "  }\n"
        "}\n"
    )
    facts = walk_file("t.ts", "typescript", src.encode()).perf_fn_facts
    targets = {name for f in facts for name, _ in f.loop_call_targets}
    assert "retryFromError" not in targets
    assert "log" in targets
