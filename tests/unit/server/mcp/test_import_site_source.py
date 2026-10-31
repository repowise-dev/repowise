"""An import site on a wrapped import line names the module it imports from."""

from __future__ import annotations

from repowise.server.mcp_server._edit_sites import _import_source

TS = """import type { Hono } from '../hono'
import {
  buildSearchParams,
  mergePath,
  replaceUrlParam,
} from './utils'
const x = mergePath('/a', 'b')
""".splitlines()

PY = """from httpx._utils import (
    primitive_value_to_str,
    to_bytes,
)
from ._utils import primitive_value_to_str as pv
""".splitlines()


def test_wrapped_ts_import_names_its_module():
    assert _import_source(TS, 4) == "./utils"


def test_wrapped_python_import_names_its_module():
    assert _import_source(PY, 2) == "httpx._utils"


def test_a_line_that_already_names_the_module_gets_nothing():
    assert _import_source(TS, 1) is None
    assert _import_source(PY, 5) is None


def test_an_unclosed_bracket_does_not_borrow_the_next_statements_module():
    lines = ["import {", "  a, // (", "}", "import { b } from './b'"]
    assert _import_source(lines, 2) is None
