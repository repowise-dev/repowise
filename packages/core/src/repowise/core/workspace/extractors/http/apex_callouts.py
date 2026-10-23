"""Salesforce Apex HTTP callout consumer dialect.

An Apex callout is a ``System.HttpRequest`` configured statement by statement::

    HttpRequest req = new HttpRequest();
    req.setEndpoint('callout:Weather_Endpoint/weather');
    req.setMethod('GET');
    new Http().send(req);

A row needs both halves inside one method: an ``HttpRequest`` declared there
(or taken as a parameter), its ``setEndpoint`` and exactly one literal verb
across its ``setMethod`` calls. A verb held in a variable, or two verbs for one
request, is refused rather than guessed.

The endpoint must be literal once names fold: an absolute URL, or
``callout:Name/path`` through a Named Credential. The credential's name becomes
the call's base token, the same unresolved-base shape as ``${API_BASE}/users``,
so ``service_bases`` can map it to a repo. An endpoint whose host or credential
is a variable (``'callout:' + name + '/x'``) is refused.

A name folds only as a ``final`` field or as a local the method assigns once.
The strings layer's file-wide folding is not used: a parameter that shares its
name with another method's local would read that local's value, which is the
caller's URL at best and a different one at worst.

Ceiling: the credential's own endpoint (``namedCredentials/*.namedCredential-meta.xml``)
is not read, because the contract walk does not read ``.xml``; reading it would
add a host and a path prefix to each ``callout:`` row.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from repowise.core.ingestion.framework_routes import apex_code

from ..base import line_at
from ..langs import APEX
from ..strings import (
    APEX_SYNTAX,
    NAME_RE,
    call_arguments,
    resolve_string,
    split_top_level,
    string_constants,
)
from .client_calls import method_from_argument
from .dialect import build_consumer_contract

if TYPE_CHECKING:
    from repowise.core.workspace.contracts import Contract

    from ..base import ScanContext

# Apex identifiers and method names are case-insensitive.
_SET_ENDPOINT_RE = re.compile(r"\b(?P<recv>[A-Za-z_]\w*)\s*\.\s*setEndpoint\s*\(", re.IGNORECASE)
_TYPE_HEADER_RE = re.compile(r"\b(?:class|interface|enum)\b", re.IGNORECASE)
# Only a test class may hold `@isTest`; a request it builds is a fixture (an
# `HttpCalloutMock` setting the endpoint it asserts on), not a call out.
_IS_TEST_RE = re.compile(r"@isTest\b", re.IGNORECASE)

# `callout:Name/path`, the name an identifier and the rest a path or query.
_CALLOUT_RE = re.compile(
    r"callout:(?P<name>[A-Za-z]\w*)(?P<rest>[/?].*)?", re.IGNORECASE | re.DOTALL
)
_ABSOLUTE_RE = re.compile(r"https?://[^/$?]+(?:[/?]|$)", re.IGNORECASE)

_CONFIDENCE = 0.75


def _blocks(code: str) -> list[tuple[int, int, bool]]:
    """``(open, close, is_type_body)`` per brace block of comment- and string-blanked *code*."""
    out: list[tuple[int, int, bool]] = []
    stack: list[tuple[int, bool]] = []
    boundary = 0  # just past the last `;`, `{` or `}`: where the current header starts
    for i, c in enumerate(code):
        if c == "{":
            stack.append((i, _TYPE_HEADER_RE.search(code, boundary, i) is not None))
            boundary = i + 1
        elif c == "}":
            if stack:
                start, is_type = stack.pop()
                out.append((start, i, is_type))
            boundary = i + 1
        elif c == ";":
            boundary = i + 1
    return out


def _method_scope(
    blocks: list[tuple[int, int, bool]], code: str, offset: int
) -> tuple[int, int] | None:
    """``(header start, body end)`` of the method around *offset*.

    The method body is the outermost enclosing block that is not a type body.
    A file with no enclosing block (an anonymous ``.apex`` script) is one scope;
    a call sitting directly in a type body is not a statement and has none.
    """
    enclosing = sorted((b for b in blocks if b[0] < offset < b[1]), key=lambda b: b[0])
    if not enclosing:
        return 0, len(code)
    for start, end, is_type in enclosing:
        if not is_type:
            # The header holds the parameter list, where a request can be declared.
            header = max(
                code.rfind(";", 0, start), code.rfind("{", 0, start), code.rfind("}", 0, start)
            )
            return header + 1, end
    return None


def _verb(content: str, code: str, recv: str, start: int, end: int) -> str | None:
    """The one literal verb *recv*'s ``setMethod`` calls name in ``[start, end)``, else None."""
    pattern = re.compile(rf"\b{re.escape(recv)}\s*\.\s*setMethod\s*\(", re.IGNORECASE)
    verbs: set[str | None] = set()
    for m in pattern.finditer(code, start, end):
        args = call_arguments(content, m.end() - 1)
        verbs.add(method_from_argument(args[0]) if args and len(args) == 1 else None)
    if len(verbs) != 1:
        return None
    return verbs.pop()


def _cut_query(expr: str) -> str:
    """*expr* up to the literal piece that opens the query string.

    What follows fills query values (``'...json?location=' + req.lat``), which
    never change the path the call reaches but often are expressions the
    strings layer refuses.
    """
    pieces = split_top_level(expr, "+")
    for i, piece in enumerate(pieces):
        if piece.strip().startswith("'") and "?" in piece:
            return "+".join(pieces[: i + 1])
    return expr


def _local_value(content: str, code: str, name: str, start: int, end: int) -> str | None:
    """The right-hand side of *name*'s one assignment in ``[start, end)``, else None."""
    pattern = rf"(?<![.\w]){re.escape(name)}\s*(\+?)=(?!=)"
    assigns = list(re.finditer(pattern, code[start:end], re.IGNORECASE))
    if len(assigns) != 1 or assigns[0].group(1):
        return None
    rhs = start + assigns[0].end()
    semi = code.find(";", rhs, end)
    return content[rhs:semi] if semi != -1 else None


def _final_fields(content: str, code: str) -> dict[str, str]:
    """The file's single-assignment string names that are declared ``final``."""
    return {
        name: rhs
        for name, rhs in string_constants(content, APEX_SYNTAX).items()
        if re.search(rf"\bfinal\b[^;={{}}]*\b{re.escape(name)}\s*=", code, re.IGNORECASE)
    }


def _endpoint(url: str) -> str | None:
    """*url* as the consumer layer reads it, or None when its target is not literal."""
    callout = _CALLOUT_RE.fullmatch(url)
    if callout is not None:
        return "${" + callout.group("name") + "}" + (callout.group("rest") or "")
    if _ABSOLUTE_RE.match(url):
        return url
    return None


class ApexCalloutsDialect:
    name = "apex-callouts"
    extensions = APEX

    def extract(self, ctx: ScanContext) -> list[Contract]:
        content = ctx.content
        if "setendpoint" not in content.lower():
            return []
        code = apex_code(content)
        if _IS_TEST_RE.search(code):
            return []
        blocks = _blocks(code)
        constants: dict[str, str] | None = None
        out: list[Contract] = []
        for m in _SET_ENDPOINT_RE.finditer(code):
            recv = m.group("recv")
            scope = _method_scope(blocks, code, m.start())
            if scope is None:
                continue
            start, end = scope
            declared = re.compile(rf"\bHttpRequest\s+{re.escape(recv)}\b", re.IGNORECASE)
            if declared.search(code, start, end) is None:
                continue  # not a request this method holds
            method = _verb(content, code, recv, start, end)
            args = call_arguments(content, m.end() - 1)
            if method is None or not args or len(args) != 1:
                continue
            if constants is None:
                constants = _final_fields(content, code)
            expr = args[0]
            if NAME_RE.fullmatch(expr) and expr not in constants:
                expr = _local_value(content, code, expr, start, end) or expr
            url = resolve_string(_cut_query(expr), APEX_SYNTAX, constants)
            endpoint = _endpoint(url) if url is not None else None
            if endpoint is None:
                continue
            c = build_consumer_contract(
                ctx,
                method=method,
                url=endpoint,
                client="apex-callout",
                line=line_at(content, m.start()),
                confidence=_CONFIDENCE,
            )
            if c is not None:
                c.symbol_name = f"apex-callout:{method} {url}"  # as written, not rewritten
                out.append(c)
        return out
