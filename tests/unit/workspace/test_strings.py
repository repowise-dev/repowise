"""The shared string-expression layer: argument selection and syntax lookup."""

from __future__ import annotations

import pytest

from repowise.core.workspace.extractors.strings import (
    JS_SYNTAX,
    PHP_SYNTAX,
    PYTHON_SYNTAX,
    Arg,
    call_arguments,
    inline_names,
    resolve_argument,
    resolve_string,
    select_argument,
    string_constants,
    syntax_for_suffix,
)


class TestSelectArgument:
    @pytest.mark.parametrize(
        ("args", "arg", "expected"),
        [
            (["'a'", "'b'"], Arg(pos=1), ["'b'"]),
            (["queue='jobs'", "cb"], Arg(keys=("queue",), pos=0), ["'jobs'"]),
            (["{ topic: 'a', messages }"], Arg(keys=("topic",)), ["'a'"]),
            (['{"topic": "a"}'], Arg(keys=("topic",)), ['"a"']),
            (["['queue' => 'jobs', 'delay' => 5]"], Arg(keys=("queue",)), ["'jobs'"]),
            (['topics = {"a", "b"}'], Arg(keys=("topics",)), ['"a"', '"b"']),
            (["['orders:a', 'orders:b']"], Arg(pos=0), ["'orders:a'", "'orders:b'"]),
            (["'a'"], Arg(pos=3), []),
            (["x == 'a'"], Arg(keys=("x",)), []),
            (['new[] { "a", "b" }'], Arg(pos=0), ['"a"', '"b"']),
            (['new List<string> { "a" }'], Arg(pos=0), ['"a"']),
            (["ctx", '[]string{"a", "b"}'], Arg(pos=1), ['"a"', '"b"']),
            (["'a'", "'b'", "group_id='g'"], Arg(pos=0, variadic=True), ["'a'", "'b'"]),
            (['"a"', '[]string{"b"}'], Arg(pos=0, variadic=True), ['"a"', '"b"']),
        ],
    )
    def test_selection(self, args: list[str], arg: Arg, expected: list[str]) -> None:
        assert select_argument(args, arg) == expected


def test_a_composite_literal_reads_its_fields_as_arguments() -> None:
    src = 'm := &sarama.ProducerMessage{Topic: "orders", Value: enc(f(x))}'
    assert call_arguments(src, src.index("{")) == ['Topic: "orders"', "Value: enc(f(x))"]


class TestResolveArgument:
    def test_a_constant_folds(self) -> None:
        got = resolve_argument(["QUEUE"], Arg(pos=0), PYTHON_SYNTAX, {"QUEUE": "'jobs'"})
        assert got == (["jobs"], False)

    def test_a_template_with_a_hole_is_refused(self) -> None:
        assert resolve_argument(["`jobs.${env}`"], Arg(pos=0), JS_SYNTAX, {}) == ([], True)

    def test_an_absent_argument_is_not_a_refusal(self) -> None:
        assert resolve_argument(["'a'"], Arg(pos=2), JS_SYNTAX, {}) == ([], False)

    def test_php_map_value(self) -> None:
        got = resolve_argument(["['queue' => 'jobs']"], Arg(keys=("queue",)), PHP_SYNTAX, {})
        assert got == (["jobs"], False)

    def test_normalize_runs_before_the_hole_check(self) -> None:
        got = resolve_argument(
            ["`${base}/orders`"], Arg(pos=0), JS_SYNTAX, {}, lambda v: v.rpartition("/")[2]
        )
        assert got == (["orders"], False)

    def test_normalize_may_refuse(self) -> None:
        assert resolve_argument(["'x'"], Arg(pos=0), JS_SYNTAX, {}, lambda v: None) == ([], True)


def _js(src: str, expr: str) -> str | None:
    return resolve_string(expr, JS_SYNTAX, string_constants(src, JS_SYNTAX))


class TestJsConstants:
    def test_a_const_folds(self) -> None:
        assert _js("export const QUEUE = 'orders';\n", "QUEUE") == "orders"

    def test_as_const_and_a_trailing_comment_are_not_the_value(self) -> None:
        assert _js("const Q = 'orders' as const; // the queue\n", "Q") == "orders"

    def test_a_typed_const_folds(self) -> None:
        assert _js("const Q: string = 'orders';\n", "Q") == "orders"

    def test_let_and_var_are_not_folded(self) -> None:
        src = "let A = 'a';\nvar B = 'b';\nA = 'c';\n"
        assert _js(src, "A") is None
        assert _js(src, "B") is None

    def test_a_const_declared_twice_is_refused(self) -> None:
        assert _js("const Q = 'a';\nfunction f() { const Q = 'b'; }\n", "Q") is None

    def test_a_const_built_by_concatenation(self) -> None:
        assert _js("const BASE = '/api';\nconst URL = BASE + '/users';\n", "URL") == "/api/users"

    def test_object_members_fold(self) -> None:
        src = "export const QUEUES = {\n  ticketSold: 'ticket.sold',\n  'guest-in': \"guest.in\",\n} as const;\n"
        assert _js(src, "QUEUES.ticketSold") == "ticket.sold"
        assert string_constants(src, JS_SYNTAX)["QUEUES.guest-in"] == '"guest.in"'

    def test_nested_object_members_fold(self) -> None:
        src = "const Q = Object.freeze({ orders: { created: 'orders.created' } });\n"
        assert _js(src, "Q.orders.created") == "orders.created"

    def test_enum_members_fold(self) -> None:
        src = "export enum Queue { Sold = 'ticket.sold', Plain }\n"
        assert _js(src, "Queue.Sold") == "ticket.sold"
        assert _js(src, "Queue.Plain") is None

    def test_a_comment_between_members_is_skipped(self) -> None:
        src = "const Q = {\n  a: 'x', // first\n  /* second */ b: 'y',\n};\n"
        assert (_js(src, "Q.a"), _js(src, "Q.b")) == ("x", "y")

    def test_a_member_that_is_not_a_string_is_refused(self) -> None:
        assert _js("const Q = { n: 5, f: () => 'x' };\n", "Q.n") is None

    def test_an_example_in_a_comment_is_not_a_binding(self) -> None:
        assert _js("// const Q = 'a';\n", "Q") is None

    def test_a_comment_after_the_last_member_value_is_not_the_value(self) -> None:
        src = "export const env = {\n  apiUrl: '/assets/data' // 'http://localhost:3000'\n};\n"
        assert _js(src, "env.apiUrl") == "/assets/data"

    def test_a_url_member_keeps_its_slashes(self) -> None:
        src = "const env = { api: 'http://localhost:4000' /* dev */, b: 'https://x.io//y' };\n"
        assert (_js(src, "env.api"), _js(src, "env.b")) == ("http://localhost:4000", "https://x.io//y")


def test_inline_names_fills_settled_holes_and_keeps_the_rest() -> None:
    names = {"this.base": "${environment.apiUrl}/users"}
    assert inline_names("${this.base}/${id}", names) == "${environment.apiUrl}/users/${id}"


class TestPhpConstants:
    def test_a_class_constant_folds_through_self(self) -> None:
        constants = string_constants("class A { const QUEUE = 'jobs'; }", PHP_SYNTAX)
        assert resolve_string("self::QUEUE", PHP_SYNTAX, constants) == "jobs"
        assert resolve_string("static::QUEUE", PHP_SYNTAX, constants) == "jobs"


class TestPythonConstants:
    def test_an_upper_case_constant_may_be_unspaced(self) -> None:
        constants = string_constants("class P:\n    EXCHANGE='orders'\n", PYTHON_SYNTAX)
        assert constants == {"EXCHANGE": "'orders'"}

    def test_an_unspaced_keyword_argument_is_not_an_assignment(self) -> None:
        src = "url = '/a'\nclient.get(\n    url=str(x),\n    TIMEOUT==1,\n)\n"
        assert string_constants(src, PYTHON_SYNTAX) == {"url": "'/a'"}

    def test_an_upper_case_keyword_argument_on_a_continuation_line_is_not_one(self) -> None:
        src = "c.f(\n    EXCHANGE='a',\n    ROUTING='k'\n)\n"
        assert string_constants(src, PYTHON_SYNTAX) == {}

    def test_an_instance_attribute_does_not_fold_a_bare_name(self) -> None:
        src = "def f(queue):\n    pass\nclass C:\n    def __init__(self):\n        self.queue = 'jobs'\n"
        constants = string_constants(src, PYTHON_SYNTAX)
        assert resolve_string("queue", PYTHON_SYNTAX, constants) is None
        assert resolve_string("self.queue", PYTHON_SYNTAX, constants) == "jobs"

    def test_self_and_cls_read_a_class_attribute(self) -> None:
        constants = string_constants("class P:\n    QUEUE = 'jobs'\n", PYTHON_SYNTAX)
        assert resolve_string("self.QUEUE", PYTHON_SYNTAX, constants) == "jobs"
        assert resolve_string("cls.QUEUE", PYTHON_SYNTAX, constants) == "jobs"
        assert resolve_string("f'{self.QUEUE}.dlq'", PYTHON_SYNTAX, constants) == "jobs.dlq"

    def test_an_attribute_a_method_rebinds_is_not_folded(self) -> None:
        src = "class P:\n    QUEUE = 'jobs'\n    def __init__(self, q):\n        self.QUEUE = q\n"
        constants = string_constants(src, PYTHON_SYNTAX)
        assert resolve_string("self.QUEUE", PYTHON_SYNTAX, constants) is None
        assert resolve_string("cls.QUEUE", PYTHON_SYNTAX, constants) is None

    def test_an_instance_attribute_keeps_the_module_constant_it_copies(self) -> None:
        src = "EXCHANGE = 'orders'\nclass P:\n    def __init__(self):\n        self.EXCHANGE = EXCHANGE\n"
        constants = string_constants(src, PYTHON_SYNTAX)
        assert resolve_string("EXCHANGE", PYTHON_SYNTAX, constants) == "orders"
        assert resolve_string("self.EXCHANGE", PYTHON_SYNTAX, constants) is None

    def test_self_is_not_a_member_prefix_in_js(self) -> None:
        assert resolve_string("self.QUEUE", JS_SYNTAX, {"QUEUE": "'jobs'"}) is None


@pytest.mark.parametrize(
    ("suffix", "expected"),
    [(".ts", JS_SYNTAX), (".py", PYTHON_SYNTAX), (".php", PHP_SYNTAX), (".sql", None)],
)
def test_syntax_for_suffix(suffix: str, expected: object) -> None:
    assert syntax_for_suffix(suffix) is expected


class TestJsLocalValue:
    @staticmethod
    def _at(content: str, name: str = "options") -> str | None:
        from repowise.core.workspace.extractors.strings import js_local_value

        return js_local_value(content, name, content.rindex("request("))

    def test_the_nearest_declaration_in_an_open_block(self) -> None:
        text = "function f() {\n  var options = {\n    uri: '/a',\n  };\n  request(options, cb);\n}\n"
        assert self._at(text) == "{\n    uri: '/a',\n  }"

    @pytest.mark.parametrize(
        "between",
        [
            "options.method = 'DELETE';\n",
            "Object.assign(options, extra);\n",
            "options = other;\n",
            "items.forEach(function (err, options) {\n",
            "items.forEach((e, options) => {\n",
            "items.forEach(options => {\n",
            "options['uri'] = base + '/b';\n",
            "class S {\n  save(options) {\n",
            "}\nfunction g() {\n",
        ],
    )
    def test_a_rebinding_or_closed_block_refuses(self, between: str) -> None:
        assert self._at("function f() {\n  var options = { uri: '/a' };\n" + between + "request(options);\n") is None

    def test_a_statement_continued_on_the_next_line_refuses(self) -> None:
        assert self._at("var url = '/a'\n  + '/b';\nrequest(url);\n", "url") is None
        assert self._at("var url = '/a' +\n  '/b';\nrequest(url);\n", "url") is None
        assert self._at("var url = '/a'\nrequest(url);\n", "url") == "'/a'"

    def test_a_control_header_naming_the_local_does_not_shadow_it(self) -> None:
        text = "function f() {\n  var options = { uri: '/a' };\n  if (options) {\n    request(options);\n"
        assert self._at(text) == "{ uri: '/a' }"
