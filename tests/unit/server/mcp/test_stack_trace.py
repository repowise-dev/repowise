"""Stack-trace frames in a question, mapped onto repo files."""

from __future__ import annotations

import pytest

from repowise.server.mcp_server._query_shape import is_issue_shaped
from repowise.server.mcp_server._stack_trace import Frame, map_to_repo_paths, parse_trace

PYTHON = """\
Traceback (most recent call last):
  File "/home/dev/proj/.venv/lib/python3.12/site-packages/flask/app.py", line 1536, in __call__
    return self.wsgi_app(environ, start_response)
  File "/usr/lib/python3.12/threading.py", line 1010, in run
    self._target(*self._args)
  File "<frozen importlib._bootstrap>", line 1360, in _find_and_load
  File "/home/dev/proj/src/shop/views.py", line 41, in checkout
    total = cart.total()
  File "/home/dev/proj/src/shop/cart.py", line 12, in total
    return sum(i.price for i in self.items)
TypeError: unsupported operand type(s)
"""

JS = """\
TypeError: Cannot read properties of undefined (reading 'get')
    at Hono.dispatch (file:///home/dev/hono/src/hono-base.ts:412:22)
    at /home/dev/hono/src/compose.ts:51:17
    at /home/dev/app/node_modules/undici/lib/fetch/index.js:200:5
    at processTicksAndRejections (node:internal/process/task_queues:95:5)
    at async Promise.all (index 0)
"""

JAVA = """\
Exception in thread "main" java.lang.IllegalStateException: Server already started
\tat io.javalin.Javalin.start(Javalin.java:180)
\tat java.base/java.lang.Thread.run(Thread.java:833)
\tat app//io.javalin.http.servlet.JavalinServlet.service(JavalinServlet.kt:89) ~[javalin-6.1.jar:6.1]
\tat org.eclipse.jetty.server.Server.handle(Server.java:516)
\tat kotlin.collections.CollectionsKt.first(Collections.kt:200)
\tat sun.reflect.NativeMethodAccessorImpl.invoke0(Native Method)
"""

GO = """\
panic: runtime error: invalid memory address or nil pointer dereference
[signal SIGSEGV: segmentation violation code=0x1 addr=0x0 pc=0x4b2c1a]

goroutine 1 [running]:
github.com/spf13/cobra.(*Command).execute(0xc0000f8300, {0xc000020090, 0x1, 0x1})
\t/home/dev/go/pkg/mod/github.com/spf13/cobra@v1.8.0/command.go:983 +0x8f
runtime.gopanic({0x5a1b20, 0x6c2f40})
\t/usr/local/go/src/runtime/panic.go:914 +0x21f
main.main()
\t/home/dev/proj/cmd/tool/main.go:10 +0x25
"""

RUST = """\
thread 'main' panicked at crates/searcher/src/searcher/glue.rs:123:9:
called `Option::unwrap()` on a `None` value
stack backtrace:
   0: rust_begin_unwind
             at /rustc/90b35a6239c3d8bdabc530a6a0816f7ff89a0aaf/library/std/src/panicking.rs:597:5
   1: core::option::unwrap_failed
             at /home/dev/.rustup/toolchains/stable/lib/rustlib/src/rust/library/core/src/option.rs:1985:5
   2: grep_searcher::searcher::glue::ReadByLine::run
             at ./crates/searcher/src/searcher/glue.rs:123:9
   3: serde::de::Deserialize::deserialize
             at /home/dev/.cargo/registry/src/index.crates.io-6f17d22bba15001f/serde-1.0.190/src/de/mod.rs:500:9
"""

RUST_OLD_PANIC = "thread 'main' panicked at 'index out of bounds', src/main.rs:5:5"

CSHARP = """\
System.InvalidOperationException: Sequence contains no elements
   at System.Linq.ThrowHelper.ThrowNoElementsException()
   at Polly.Retry.RetryResilienceStrategy`1.ExecuteCore[TState](Func`3 callback) in C:\\build\\Polly\\src\\Polly.Core\\Retry\\RetryResilienceStrategy.cs:line 54
   at Polly.ResiliencePipeline.ExecuteAsync() in /_/src/Polly.Core/ResiliencePipeline.Async.cs:line 31
   at Microsoft.Extensions.Http.Something.Send()
"""


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (
            PYTHON,
            [
                Frame("/home/dev/proj/src/shop/cart.py", 12, "total"),
                Frame("/home/dev/proj/src/shop/views.py", 41, "checkout"),
                Frame("flask/app.py", 1536, "__call__", True),
            ],
        ),
        (
            JS,
            [
                Frame("/home/dev/hono/src/hono-base.ts", 412, "Hono.dispatch"),
                Frame("/home/dev/hono/src/compose.ts", 51, None),
                Frame("undici/lib/fetch/index.js", 200, None, True),
            ],
        ),
        (
            JAVA,
            [
                Frame("io/javalin/Javalin.java", 180, "io.javalin.Javalin.start"),
                Frame(
                    "io/javalin/http/servlet/JavalinServlet.kt",
                    89,
                    "io.javalin.http.servlet.JavalinServlet.service",
                ),
                Frame(
                    "org/eclipse/jetty/server/Server.java", 516, "org.eclipse.jetty.server.Server.handle"
                ),
            ],
        ),
        (
            GO,
            [
                Frame("/home/dev/proj/cmd/tool/main.go", 10, "main.main"),
                Frame(
                    "github.com/spf13/cobra/command.go",
                    983,
                    "github.com/spf13/cobra.(*Command).execute",
                    True,
                ),
            ],
        ),
        (
            RUST,
            [
                Frame("crates/searcher/src/searcher/glue.rs", 123, None),
                Frame("crates/searcher/src/searcher/glue.rs", 123, "grep_searcher::searcher::glue::ReadByLine::run"),
                Frame("src/de/mod.rs", 500, "serde::de::Deserialize::deserialize", True),
            ],
        ),
        (RUST_OLD_PANIC, [Frame("src/main.rs", 5, None)]),
        (
            CSHARP,
            [
                Frame(
                    "C:/build/Polly/src/Polly.Core/Retry/RetryResilienceStrategy.cs",
                    54,
                    "Polly.Retry.RetryResilienceStrategy`1.ExecuteCore[TState]",
                ),
                Frame(
                    "/_/src/Polly.Core/ResiliencePipeline.Async.cs", 31, "Polly.ResiliencePipeline.ExecuteAsync"
                ),
            ],
        ),
    ],
    ids=["python", "js", "java", "go", "rust", "rust_old_panic", "csharp"],
)
def test_parse_trace(text: str, expected: list[Frame]) -> None:
    assert parse_trace(text) == expected


def test_plain_question_has_no_frames() -> None:
    assert parse_trace("How does the router match a path like /users/:id at runtime?") == []


INDEXED = [
    "src/shop/cart.py",
    "src/shop/views.py",
    "src/flask/app.py",
    "src/hono-base.ts",
    "src/compose.ts",
    "javalin/src/main/java/io/javalin/Javalin.java",
    "javalin/src/main/java/io/javalin/http/servlet/JavalinServlet.kt",
    "command.go",
    "crates/searcher/src/searcher/glue.rs",
    "src/Polly.Core/Retry/RetryResilienceStrategy.cs",
    "src/Polly.Core/ResiliencePipeline.Async.cs",
    "a/util.py",
    "b/util.py",
    "c/util.py",
    "d/util.py",
]


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (PYTHON, ["src/shop/cart.py", "src/shop/views.py", "src/flask/app.py"]),
        (JS, ["src/hono-base.ts", "src/compose.ts"]),
        (
            JAVA,
            [
                "javalin/src/main/java/io/javalin/Javalin.java",
                "javalin/src/main/java/io/javalin/http/servlet/JavalinServlet.kt",
            ],
        ),
        (GO, ["command.go"]),
        (RUST, ["crates/searcher/src/searcher/glue.rs"]),
        (
            CSHARP,
            ["src/Polly.Core/Retry/RetryResilienceStrategy.cs", "src/Polly.Core/ResiliencePipeline.Async.cs"],
        ),
    ],
    ids=["python", "js", "java", "go", "rust", "csharp"],
)
def test_map_to_repo_paths(text: str, expected: list[str]) -> None:
    assert map_to_repo_paths(parse_trace(text), INDEXED) == expected


def test_ambiguous_basename_kept_up_to_three() -> None:
    three = [Frame("util.py", 1, None)]
    assert map_to_repo_paths(three, INDEXED[11:14]) == ["a/util.py", "b/util.py", "c/util.py"]
    assert map_to_repo_paths(three, INDEXED[11:]) == []


def test_unrelated_absolute_frame_does_not_match_by_basename() -> None:
    frames = [Frame("/home/dev/other/app.py", 3, "main")]
    assert map_to_repo_paths(frames, ["src/flask/app.py"]) == []


NAMES = frozenset({"AuthService", "authservice", "login"})


class TestIsIssueShaped:
    def test_trace(self) -> None:
        assert is_issue_shaped(RUST_OLD_PANIC, set())

    def test_defined_identifier(self) -> None:
        assert is_issue_shaped("Why does AuthService reject a valid token?", NAMES)

    def test_code_shaped_but_undefined(self) -> None:
        assert not is_issue_shaped("Why does TokenRefreshScheduler stall?", NAMES)

    def test_capitalised_english_word_is_not_an_identifier(self) -> None:
        names = NAMES | {"Config", "config"}
        assert not is_issue_shaped("Why does `Config` reject a valid token?", names)

    def test_shares_parsed_frames(self) -> None:
        assert is_issue_shaped("no trace text here", set(), frames=[Frame("a.py", 1, None)])

    def test_plain_question(self) -> None:
        assert not is_issue_shaped("How are tokens refreshed?", NAMES)


def test_input_is_bounded() -> None:
    frame = '  File "/srv/app/x.py", line 1, in f\n'
    assert parse_trace("x\n" * 15_000 + frame) == []  # past the first 20k chars
    assert parse_trace(frame.rstrip("\n") + " " * 600) == []
    many = "".join(f'  File "/srv/app/m{i}.py", line 1, in f\n' for i in range(100))
    deps = "".join(f'  File "/v/site-packages/d/m{i}.py", line 1, in f\n' for i in range(50))
    frames = parse_trace(many + deps)
    assert sum(not f.vendored for f in frames) == 40
    assert sum(f.vendored for f in frames) == 20
    assert frames[0].path == "/srv/app/m99.py"  # innermost kept


def test_generic_and_plain_words_are_not_looked_up() -> None:
    from repowise.server.mcp_server.tool_search_symbols import _named_lookups

    names = {"config", "Result", "result", "load_config", "AuthService", "authservice"}
    text = "app.config is None in load_config; Result comes back empty from AuthService"
    assert _named_lookups(text, names) == ["load_config", "AuthService"]
