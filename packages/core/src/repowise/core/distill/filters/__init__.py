"""Distill output filters.

Importing this package registers every built-in filter with the shared
``filter_registry``. Adding a filter = new module here + the decorator;
the router and engine never change.
"""

from __future__ import annotations

from repowise.core.distill.filters import (  # noqa: F401  (registration side effect)
    build_output,
    file_listing,
    git_diff,
    git_log,
    git_status,
    logs,
    search_results,
    test_output,
)
from repowise.core.distill.filters.base import ERROR_LINE_RE, OutputFilter, is_error_line

__all__ = ["ERROR_LINE_RE", "OutputFilter", "is_error_line"]
