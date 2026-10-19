"""Python imports."""

from __future__ import annotations

from ..bindings.python import expand_bare_relative_imports
from ._types import ImportExtractor
from .generic import generic_statement

PYTHON_IMPORTS = ImportExtractor(statement=generic_statement, finalize=expand_bare_relative_imports)
