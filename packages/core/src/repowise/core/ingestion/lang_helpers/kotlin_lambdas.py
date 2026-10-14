"""Is a Kotlin call site inside a lambda of its function?

A lambda may carry a receiver (`routing { get("/") }`, `LazyColumn { item() }`)
whose type a text scan cannot see, so code there may have any implicit
``this``. Code directly in a function body has only the function's own
receivers. A brace opens a lambda unless it is the function body or a
control-flow block.
"""

from __future__ import annotations

import re

_STRING = re.compile(r'"(?:\\.|[^"\\\n])*"|\'(?:\\.|[^\'\\\n])\'')
_LINE_COMMENT = re.compile(r"//[^\n]*")
_BLOCK_WORDS = re.compile(r"(?:\b(?:else|try|finally|do|init|when)|->)$")
_BLOCK_HEADS = frozenset({"if", "for", "while", "when", "catch"})
_HEAD_WORD = re.compile(r"(\w+)\s*$")
_PARENTHESIZED = re.compile(r"\([^()]*\)")


def _paren_head(text: str, end: int) -> str | None:
    """The word before the `(` matching the `)` just before *end*."""
    depth = 0
    index = end
    while index > 0:
        index -= 1
        depth += (text[index] == ")") - (text[index] == "(")
        if depth == 0:
            break
    if depth:
        return None
    word = _HEAD_WORD.search(text, max(0, index - 40), index)
    return word.group(1) if word else None


def _opens_block(text: str, end: int) -> bool:
    """Does a `{` at *end* open a control-flow block rather than a lambda?"""
    while end and text[end - 1].isspace():
        end -= 1
    if _BLOCK_WORDS.search(text, max(0, end - 16), end):
        return True
    return text[end - 1 : end] == ")" and _paren_head(text, end) in _BLOCK_HEADS


def _is_block_body(header: str) -> bool:
    """Does the first brace after *header* open a block body, not an expression body's lambda?

    An expression body is the only `=` outside the header's parentheses.
    """
    while True:
        stripped = _PARENTHESIZED.sub("", header)
        if stripped == header:
            return "=" not in header
        header = stripped


def in_lambda(source: str, has_body: bool) -> bool:
    """Is the end of *source*, a function's text up to a call, inside a lambda?

    *has_body* says the function's first brace is its block body, not a
    lambda of an expression body. Ceiling: a raw string holding a brace
    miscounts.
    """
    text = _LINE_COMMENT.sub("", _STRING.sub('""', source))
    stack: list[bool] = []
    body_seen = not has_body
    for match in re.finditer(r"[{}]", text):
        if match.group() == "}":
            if stack:
                stack.pop()
            continue
        if not body_seen:
            body_seen = True
            if _is_block_body(text[: match.start()]):
                stack.append(False)
                continue
        stack.append(not _opens_block(text, match.start()))
    return any(stack)
