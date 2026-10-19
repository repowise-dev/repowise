"""Analysed repo source with an invalid escape must not leak SyntaxWarnings."""

from __future__ import annotations

import warnings

from repowise.core.ingestion.framework_edges.test_path_strings import python_ancestor_targets
from repowise.core.pyparse import parse_python, symtable_python

SOURCE = 'import re\nPATTERN = re.compile("\\s+")\n'


def test_helpers_stay_quiet() -> None:
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        parse_python(SOURCE)
        symtable_python(SOURCE)


def test_test_path_scan_stays_quiet() -> None:
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        python_ancestor_targets("tests/test_x.py", SOURCE.encode(), {"tests/test_x.py"})
