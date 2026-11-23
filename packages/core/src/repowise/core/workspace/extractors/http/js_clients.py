"""JavaScript / TypeScript HTTP consumer dialect.

Covers direct ``fetch`` calls, the client libraries of :mod:`.node_clients`
(axios, ky, got, ofetch, request, superagent, needle, AngularJS ``$http``
and their instances), plus wrapper calls whose first
argument is a concrete URL literal, e.g. ``fetchJSON(`${BASE}/path`, { method:
"POST" })``. Wrapper detection is signal-gated (the callee name looks HTTP-ish,
or the call carries a ``method:`` option) so ordinary `/`-prefixed string
arguments, router navigation and i18n keys, are not mistaken for service calls.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from dataclasses import replace
from functools import partial
from typing import TYPE_CHECKING

from repowise.core.workspace.origins import ContractOrigin

from ..calls import FileStrings, receiver_name
from ..langs import JS_TS
from ..strings import (
    JS_SYNTAX,
    call_arguments,
    inline_names,
    literal_span,
    match_paren,
    string_constants,
)
from .angular import environment_mounts
from .client_calls import ClientCallMatch, consumer_contracts, join_base, method_from_options
from .commonjs import exports_mounts
from .node_clients import FieldScope, client_calls, client_mounts
from .wrappers import GENERIC_ARGS

if TYPE_CHECKING:
    from repowise.core.workspace.contracts import Contract

    from ..base import ScanContext

# fetch('/api/users') or fetch('/api/users', { method: 'POST' })
_FETCH_RE = re.compile(
    r"""fetch\s*\(\s*['"`]([^'"`]+)['"`]""",
)

# Wrapper call: IDENT("<url>" | `<url>`, ...) where the URL literal is concrete,
# i.e. starts with `/`, a `${...}` base placeholder, or a scheme. The scan is
# led by the `(` and reads the identifier back from it: led by the identifier,
# the regex was tried at every word of every file.
_WRAPPER_ARG_RE = re.compile(r"""\(\s*['"`]((?:/|\$\{|https?:)[^'"`]*)['"`]""")
# The callee may carry TypeScript type arguments, nested up to three levels
# (`apiGet<Paginated<Array<Commit>>>(`). Each inner level starts at a `<`, so
# the pattern cannot backtrack across levels. The `<` must follow the name
# directly, as type arguments do and a spaced comparison (`a < b && c > (`) does not.
_TYPE_ARGS = r"(?:<[^<>()]*(?:<[^<>()]*(?:<[^<>()]*>[^<>()]*)*>[^<>()]*)*>)?"
_CALLEE_BEFORE_RE = re.compile(r"\b(\w+)" + _TYPE_ARGS + r"\s*$")
_CALLEE_WINDOW = 200

# The callee names that read as an HTTP wrapper rather than navigation/util.
_HTTP_NAME_RE = re.compile(r"(?i)fetch|request|http|api|ajax|rest|rpc")

# Callees already handled elsewhere (`ofetch(` is a fetch call), or never an HTTP wrapper.
_WRAPPER_SKIP = frozenset({"fetch", "ofetch", "if", "for", "while", "switch", "catch", "return"})


def fetch_calls(content: str) -> Iterator[ClientCallMatch]:
    """``fetch(url)`` and ``fetch(url, { method })``; no method option is a GET."""
    for m in _FETCH_RE.finditer(content):
        yield ClientCallMatch(
            client="fetch",
            url=literal_span(content, m, 1),
            offset=m.start(),
            callee="fetch",
            method=_options_method(content, content.index("(", m.start())),
        )


def _options_method(content: str, paren: int) -> str | None:
    """The verb the options after the URL of the call opening at *paren* name."""
    args = call_arguments(content, paren)
    return method_from_options(args[1:]) if args else None


def _callee(content: str, start: int, end: int) -> str:
    """The whole identifier around ``content[start:end]``, a leading ``$`` included."""
    while start > 0 and (content[start - 1].isalnum() or content[start - 1] in "_$"):
        start -= 1
    return content[start:end]


def _receiver(content: str, start: int) -> str | None:
    """The name a member call at *start* is made on (``http`` in ``this.http.get``)."""
    dot = start - 1
    while dot >= 0 and content[dot] in " \t\n":
        dot -= 1
    return receiver_name(content, dot) if dot >= 0 and content[dot] == "." else None


def wrapper_calls(content: str, clients: frozenset[str]) -> Iterator[ClientCallMatch]:
    """``fetchJSON(`${BASE}/path`, { method: "POST" })`` and its relatives.

    *clients* are the client names the file calls, read with their base elsewhere.
    """
    for m in _WRAPPER_ARG_RE.finditer(content):
        head = _CALLEE_BEFORE_RE.search(content, max(0, m.start() - _CALLEE_WINDOW), m.start())
        if head is None:
            continue
        callee = head.group(1)
        if callee in _WRAPPER_SKIP or _callee(content, head.start(1), head.end(1)) in clients:
            continue
        if clients and _receiver(content, head.start(1)) in clients:
            continue  # `http.post(url, { method })`, a client's own verb
        # The options may span lines: `apiRequest(url, {\n  method: 'POST',`.
        method = _options_method(content, m.start())
        # Require an HTTP signal: an HTTP-ish callee name or a method option.
        if not (_HTTP_NAME_RE.search(callee) or method):
            continue
        # No `method:` option: the callee name is the only verb evidence there
        # is, and `apiPost`/`apiDelete` carry it.
        yield ClientCallMatch(
            client="wrapper",
            url=literal_span(content, m, 1),
            offset=head.start(),
            callee=callee,
            method=method,
            origin=ContractOrigin.NAME_HEURISTIC,
        )


# ---------------------------------------------------------------------------
# Bases a call's URL does not spell itself
# ---------------------------------------------------------------------------

# The `(` of a definition of a wrapper: a function declaration, a
# `name = (...) =>` or `name = function (...)` binding, or a method in a class
# or object body. A call matches the last form too; only a parameter list
# followed by a body is read as a definition.
_DEF_HEAD = (
    r"(?:\bfunction\s*\*?\s*{n1}\s*{g}"
    r"|(?<![\w$.]){n2}\s*(?::[^=;\n]+)?=\s*(?:async\s+)?(?:function\b\s*)?{g}"
    r"|(?:^|(?<=[{{,;]))[ \t]*(?:(?:private|public|protected|static|async|override|readonly)\s+)*"
    r"{n3}\s*{g})\("
)
# After the parameter list: an optional return type with no brace in it, then
# the body's `{`, or an arrow straight into a template.
_BODY_START_RE = re.compile(r"\s*(?::[^{;=`]*?)?\s*(?:=>\s*)?(?P<open>[{`])")
_TEMPLATE_RE = re.compile(r"`[^`]*`")
_FIRST_PARAM_RE = re.compile(r"\s*(?:\.\.\.)?([A-Za-z_$][\w$]*)")
# One leading base placeholder (`${API_BASE_URL}/api/x`).
_LEAD_HOLE_RE = re.compile(r"\$\{([^{}]+)\}")
# A field read from the environment is a host, the base placeholder it always was.
_ENV_RE = re.compile(r"(?:process\.env|import\.meta\.env)\b")
# Statement text before a template that makes it something other than the URL
# the wrapper requests: a nested callback, a log line, an error message.
_NOT_A_REQUEST_RE = re.compile(
    r"=>|\bfunction\b|\bconsole\.|\bthrow\b|Error\s*\(|\b(?:log|logger)\s*[.(]|\bwarn\s*\("
)


def with_bases(ctx: ScanContext, matches: list[ClientCallMatch]) -> list[ClientCallMatch]:
    """*matches* with the bases their URLs leave out composed back on.

    Two places hold a base a call does not spell: a class field the URL reads
    (`` `${this.baseUrl}/x` ``, set once to a constant), and a wrapper this
    file defines that puts a prefix before its path argument (``apiRequest(path)``
    building `` `/api/resume${path}` ``). A call whose base is one of those but
    does not resolve is dropped and counted as unresolved, never matched on its
    path alone. A field read from the environment stays the host placeholder.
    """
    content = ctx.content
    callees = {m.callee for m in matches if m.client == "wrapper"}
    if not callees and not any("this." in m.url for m in matches):
        return matches
    strings = FileStrings(content, JS_SYNTAX)
    fields = FieldScope(strings)
    prefixes = wrapper_prefixes(content, callees, strings, fields)
    out: list[ClientCallMatch] = []
    for m in matches:
        settled: dict[str, str] = {}
        if "this." in m.url:
            fields.enter(m.offset)
            text = strings.text(m.url)
            if text is None or unsettled_field(text, fields):
                ctx.note_unresolved(m.offset)
                continue
            settled = dict(strings.settled)
        prefix = ""
        # Only a call that reaches this file's definition: no receiver, or `this`.
        if m.client == "wrapper" and _receiver(content, m.offset) in (None, "this"):
            prefix = prefixes.get(m.callee, "")
        if prefix is None:
            ctx.note_unresolved(m.offset)
            continue
        if settled or prefix:
            m = replace(m, compose=partial(_compose, settled, prefix))
        out.append(m)
    return out


def unsettled_field(text: str, fields: FieldScope) -> bool:
    """Whether *text* starts with a class field this cannot settle and is no env host."""
    lead = _LEAD_HOLE_RE.match(text)
    name = lead.group(1).strip() if lead else ""
    if not name.startswith("this."):
        return False
    raw = fields.raw(name)
    return raw is None or _ENV_RE.match(raw) is None


def based_url(url: str | None, prefix: str | None, fields: FieldScope) -> str | None:
    """*url* with a wrapper's *prefix* put on; None when either base cannot be read."""
    if url is None or prefix is None or unsettled_field(url, fields):
        return None
    return join_base(prefix, url) if prefix and url.startswith("/") else url


def _compose(settled: dict[str, str], prefix: str, url: str) -> str:
    url = inline_names(url, settled) if settled else url
    return join_base(prefix, url) if prefix and url.startswith("/") else url


def wrapper_prefixes(
    content: str, names: set[str], strings: FileStrings, fields: FieldScope
) -> dict[str, str | None]:
    """``{wrapper name: prefix}`` for each of *names* this file defines with one.

    The prefix is what the wrapper's body puts before its first parameter in
    the URL it requests. ``None`` when that cannot be read (a field set from a
    parameter, a reassigned or destructured parameter, two candidates or two
    definitions that disagree). A name the file does not define, or whose
    body adds no prefix, is absent.
    """
    if not names:
        return {}
    alt = "|".join(sorted((re.escape(n) for n in names), key=len, reverse=True))
    head = re.compile(
        _DEF_HEAD.format(
            n1=f"(?P<n1>{alt})", n2=f"(?P<n2>{alt})", n3=f"(?P<n3>{alt})", g=GENERIC_ARGS
        ),
        re.MULTILINE,
    )
    found: dict[str, set[str | None]] = {}
    for m in head.finditer(content):
        name = m.group("n1") or m.group("n2") or m.group("n3")
        found.setdefault(name, set()).add(_definition_prefix(content, m.end() - 1, strings, fields))
    out: dict[str, str | None] = {}
    for name, prefixes in found.items():
        prefixes.discard("")
        if prefixes:
            out[name] = prefixes.pop() if len(prefixes) == 1 else None
    return out


def _definition_prefix(
    content: str, paren: int, strings: FileStrings, fields: FieldScope
) -> str | None:
    """The prefix the definition whose parameter list opens at *paren* adds ("" for none)."""
    found = _definition_body(content, paren)
    if not isinstance(found, tuple):
        return found
    name, start, end, arrow_template = found
    if re.search(rf"(?<![\w$.]){re.escape(name)}\s*(?:\+|\|\||\?\?)?=(?![=>])", content[start:end]):
        return None  # reassigned: the path the call passes is not what is sent
    fields.enter(start)
    candidates = {
        _template_prefix(text, name, strings, fields)
        for text in _url_templates(content, name, start, end, arrow_template)
    }
    candidates.discard("")
    if not candidates:
        return ""
    return candidates.pop() if len(candidates) == 1 else None


def _definition_body(content: str, paren: int) -> tuple[str, int, int, bool] | str | None:
    """``(first parameter, body start, body end, arrow template)`` of the
    definition whose parameter list opens at *paren*; "" when it is no
    definition worth reading, None when its parameter cannot be read."""
    close = match_paren(content, paren)
    body = _BODY_START_RE.match(content, close + 1) if close > 0 else None
    params = content[paren + 1 : close].strip() if body else ""
    if not params:
        return ""  # a call, a signature with no body, or nothing to prefix
    param = _FIRST_PARAM_RE.match(params)
    if param is None:
        return None  # destructured: what the URL is cannot be read
    start = body.start("open")
    arrow_template = body.group("open") == "`"
    end = start + 1 if arrow_template else match_paren(content, start, closer="}")
    return (param.group(1), start, end, arrow_template) if end > 0 else ""


def _url_templates(
    content: str, name: str, start: int, end: int, arrow_template: bool
) -> Iterator[str]:
    """The templates in a wrapper body that put *name* into the URL it requests."""
    hole = re.compile(r"\$\{\s*" + re.escape(name) + r"\s*\}")
    if arrow_template:
        t = _TEMPLATE_RE.match(content, start)
        if t and hole.search(t.group(0)):
            yield t.group(0)
        return
    for t in _TEMPLATE_RE.finditer(content, start, end):
        if hole.search(t.group(0)) and _requested(content, start, t.start()):
            yield t.group(0)
    concat = re.compile(rf"""(['"])([^'"\n]*)\1\s*\+\s*{re.escape(name)}\b""")
    for c in concat.finditer(content, start, end):
        if _requested(content, start, c.start()):
            yield f"`{c.group(2)}${{{name}}}`"


def _requested(content: str, body: int, at: int) -> bool:
    """Whether the expression at *at* sits in the body's own statements, not a
    nested function or block, a callback, a log line or an error message."""
    inner = content[body + 1 : at]
    if inner.count("{") - inner.count("}") != 0:
        return False
    statement = re.split(r"[;{}\n]", inner)[-1]
    return _NOT_A_REQUEST_RE.search(statement) is None


def _template_prefix(
    template: str, param: str, strings: FileStrings, fields: FieldScope
) -> str | None:
    """What *template* puts before ``${param}``: "" when not a URL prefix, None when unresolved."""
    text = strings.text(template)
    hole = "${" + param + "}"
    if text is None or text.count(hole) != 1:
        return ""
    before, after = text.split(hole)
    if after and not after.startswith(("?", "${")):
        return ""  # the argument sits inside a path, not at its end
    if unsettled_field(before, fields):
        return None
    lead = _LEAD_HOLE_RE.match(before)
    path = before[lead.end() :] if lead else before
    if "${" in path:
        return None
    if path.startswith("/") or (not lead and path.startswith(("http://", "https://"))):
        return before.rstrip("/")
    return ""


class JsClientsDialect:
    name = "js-clients"
    extensions = JS_TS
    # Next to no JS file lacks one of these: the URL literal a fetch or a
    # wrapper reads, the import a client library or an imported instance needs,
    # or the export an environment module is. Recorded as the dialect's
    # ceiling: this gate is the extension, near enough.
    requires = ("fetch", "'/", '"/', "`/", "${", "http", "axios", "import", "require", "export")

    def collect_mounts(self, ctx: ScanContext) -> dict[str, str]:
        return client_mounts(ctx) | environment_mounts(ctx) | exports_mounts(ctx)

    def extract(self, ctx: ScanContext) -> list[Contract]:
        content = ctx.content
        from_clients, clients = client_calls(ctx)
        # A client named like `apifetch` is read with its base above, not as `fetch`.
        fetches = [m for m in fetch_calls(content) if _callee(content, m.offset, m.offset + 5) not in clients]
        matches = with_bases(ctx, [*fetches, *wrapper_calls(content, clients)])
        # A `${BASE}` interpolation folds when the file binds BASE to a constant.
        constants = string_constants(content, JS_SYNTAX) if any("${" in m.url for m in matches) else {}
        return from_clients + consumer_contracts(ctx, matches, JS_SYNTAX, constants=constants)
