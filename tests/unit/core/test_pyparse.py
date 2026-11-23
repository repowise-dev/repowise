"""Analysed repo source with an invalid escape must not leak SyntaxWarnings."""

from __future__ import annotations

import re
import warnings
from pathlib import Path

from repowise.core.analysis.test_reach.mocks import replaced_modules
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


def test_mock_scan_stays_quiet() -> None:
    """#6036: init's test masks parsed a test with "\\;" through ast.parse and printed the warning."""
    test = (
        "from unittest import mock\n\n"
        "def test_clean():\n"
        '    cmd = "find . -exec rm {} \\;"\n'
        '    with mock.patch("svc.job.os.system"):\n'
        "        clean()\n"
    )
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        assert "svc.job.os.system" in replaced_modules("tests/test_job.py", test)


def test_repo_source_is_parsed_through_parse_python() -> None:
    """A direct ast.parse of the analysed repo's source prints its warnings; pyparse is the one door."""
    core = Path(__file__).resolve().parents[3] / "packages" / "core" / "src" / "repowise" / "core"
    direct = sorted(
        str(path.relative_to(core))
        for path in core.rglob("*.py")
        if path.name != "pyparse.py"
        and re.search(r"\bast\.parse\(", path.read_text(encoding="utf-8"))
    )
    assert direct == []
