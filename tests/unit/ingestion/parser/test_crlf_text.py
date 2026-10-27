"""Stored symbol text must not depend on the checkout's line-ending setting.

Covers #3912: a file committed with CRLF (or a Windows checkout running with
``core.autocrlf=true``) used to keep the literal ``\\r\\n`` inside
``Symbol.signature`` and ``Symbol.docstring``, so the same symbol read
differently from one MCP tool to the next.
"""

from __future__ import annotations

import pytest

from repowise.core.ingestion.parser import ASTParser
from tests.unit.ingestion.parser._helpers import _make_file_info

# The repro from the issue, byte for byte: a multi-line signature whose docstring
# is also multi-line, with CRLF endings throughout.
CRLF_SOURCE = (
    b'"""Module docstring.\r\n'
    b'\r\n'
    b'Second line.\r\n'
    b'"""\r\n'
    b'\r\n'
    b'\r\n'
    b'def run_update(\r\n'
    b'    path: str | None,\r\n'
    b'    force: bool = False,\r\n'
    b') -> int:\r\n'
    b'    """Compute a budget.\r\n'
    b'\r\n'
    b'    Small changes get a small budget.\r\n'
    b'    """\r\n'
    b'    return 1\r\n'
)

LF_SOURCE = CRLF_SOURCE.replace(b'\r\n', b'\n')

PATH = 'pkg/mod.py'
LANGUAGE = 'python'


@pytest.fixture(scope='module')
def crlf_result():
    return ASTParser().parse_file(_make_file_info(PATH, LANGUAGE), CRLF_SOURCE)


@pytest.fixture(scope='module')
def lf_result():
    return ASTParser().parse_file(_make_file_info(PATH, LANGUAGE), LF_SOURCE)


def _symbol(result):
    return next(symbol for symbol in result.symbols if symbol.name == 'run_update')


def test_signature_has_no_carriage_returns(crlf_result) -> None:
    """A CRLF file's signature is stored with LF, like an LF file's."""
    signature = _symbol(crlf_result).signature

    assert '\r' not in signature
    assert signature.startswith('def run_update(')


def test_symbol_docstring_has_no_carriage_returns(crlf_result) -> None:
    """A CRLF file's symbol docstring is stored with LF."""
    docstring = _symbol(crlf_result).docstring

    assert docstring is not None
    assert '\r' not in docstring
    assert docstring.startswith('Compute a budget.')


def test_module_docstring_has_no_carriage_returns(crlf_result) -> None:
    """A CRLF file's module docstring is stored with LF."""
    docstring = crlf_result.docstring

    assert docstring is not None
    assert '\r' not in docstring
    assert docstring.startswith('Module docstring.')


def test_lf_source_is_unchanged(crlf_result, lf_result) -> None:
    """A CRLF checkout and an LF checkout of one file store the same text."""
    assert _symbol(crlf_result).signature == _symbol(lf_result).signature
    assert _symbol(crlf_result).docstring == _symbol(lf_result).docstring
    assert crlf_result.docstring == lf_result.docstring
