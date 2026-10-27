"""Decision records anchored in excluded paths never surface as "key decisions".

Decision mining can predate an exclude_patterns / info-exclude change, so the
DB may hold records mined from vendored trees (a checked-in venv's
site-packages). Read-time filtering heals existing indexes without a reindex.
"""

from __future__ import annotations

import json
import types

import pathspec

from repowise.server.mcp_server._helpers import decision_is_excluded

_SPEC = pathspec.PathSpec.from_lines("gitwildmatch", ["research/", "*.lock"])


def _decision(affected: list[str] | None, status: str = "proposed"):
    return types.SimpleNamespace(
        affected_files_json=json.dumps(affected) if affected is not None else None,
        status=status,
    )


def test_a_confirmed_record_is_never_junk():
    """The file list says where a decision was read, not what makes it true.

    Measured on the dogfooded repo: all 18 records this rule dropped were
    ``active``, a quarter of the confirmed corpus, and among them were both
    records answering "why ruff check and not ruff format" — mined from a
    local-only notes tree, and so plainly repo-wide that the same rule had been
    written into CLAUDE.md by hand. What the rule is aimed at is unreviewed
    machine output from a tree the user said to ignore, and ``proposed`` is
    what marks that.
    """
    files = ["research/.pbvenv/Lib/site-packages/charset_normalizer/api.py"]
    assert decision_is_excluded(_decision(files, status="active"), _SPEC) is False
    assert decision_is_excluded(_decision(files, status="proposed"), _SPEC) is True


def test_all_affected_files_excluded_is_junk():
    d = _decision(["research/.pbvenv/Lib/site-packages/charset_normalizer/api.py"])
    assert decision_is_excluded(d, _SPEC) is True


def test_windows_separators_normalize():
    d = _decision(["research\\.pbvenv\\Lib\\site-packages\\api.py"])
    assert decision_is_excluded(d, _SPEC) is True


def test_any_real_file_keeps_the_decision():
    d = _decision(["research/scratch.py", "packages/core/src/real.py"])
    assert decision_is_excluded(d, _SPEC) is False


def test_no_affected_files_is_kept():
    assert decision_is_excluded(_decision([]), _SPEC) is False
    assert decision_is_excluded(_decision(None), _SPEC) is False


def test_no_spec_keeps_everything():
    d = _decision(["research/junk.py"])
    assert decision_is_excluded(d, None) is False


def test_malformed_json_is_kept():
    d = types.SimpleNamespace(affected_files_json="{not json")
    assert decision_is_excluded(d, _SPEC) is False
