"""``repowise doc-drift`` reads persisted findings and never invents a clean tree.

The refusal cases carry most of the weight here. An empty finding list is the
detector's *good* answer, so every way of failing to read one has to be
distinguishable from it — otherwise a broken index reports as tidy documentation.
"""

from __future__ import annotations

import json

import pytest
from click.testing import CliRunner
from sqlalchemy.exc import OperationalError

from repowise.cli.commands import doc_drift_cmd
from repowise.core.analysis.doc_drift.constants import DETECTION_BASIS
from repowise.core.persistence.crud import serialize_doc_drift_row


class _Row:
    """A ``DocDriftFinding`` row, only the attributes the command reads."""

    def __init__(self, **kw):
        self.file_path = kw.get("file_path", "docs/a.md")
        self.line_number = kw.get("line_number", 7)
        self.kind = kw.get("kind", "path")
        self.target = kw.get("target", "src/gone.py")
        self.confidence = kw.get("confidence", 0.9)
        self.origin = kw.get("origin", "path_no_candidate")
        self.reason = kw.get("reason", "Document names src/gone.py, which no longer exists.")
        self.evidence_json = kw.get("evidence_json", '["docs/a.md:7 states `src/gone.py`"]')
        self.raw = kw.get("raw", "src/gone.py")
        self.context = kw.get("context", "see src/gone.py")


def _invoke(monkeypatch, tmp_path, result, args=()):
    monkeypatch.setattr(doc_drift_cmd, "_repo_path", lambda *a, **k: tmp_path)
    def _run(coro):
        # Close it rather than leaking an un-awaited coroutine warning, the way
        # ``test_tool_adapter_commands._spy_run`` does.
        coro.close()
        return result

    monkeypatch.setattr(doc_drift_cmd, "run_async", _run)
    return CliRunner().invoke(doc_drift_cmd.doc_drift_command, list(args))


def test_findings_render_with_document_line_and_evidence(monkeypatch, tmp_path):
    rows = [serialize_doc_drift_row(_Row())]
    result = _invoke(monkeypatch, tmp_path, rows)

    assert result.exit_code == 0
    assert "docs/a.md" in result.output
    assert ":7" in result.output
    # The evidence line is the thing a reader checks for themselves.
    assert "states" in result.output


def test_json_carries_the_whole_row_and_the_basis(monkeypatch, tmp_path):
    rows = [serialize_doc_drift_row(_Row())]
    result = _invoke(monkeypatch, tmp_path, rows, ["--format", "json"])

    payload = json.loads(result.output)
    assert payload["total"] == 1
    assert payload["documents"] == 1
    assert payload["confidence"] == {"high": 1, "medium": 0, "low": 0}
    assert payload["findings_basis"] == DETECTION_BASIS
    finding = payload["findings"][0]
    assert finding["file_path"] == "docs/a.md"
    assert finding["target"] == "src/gone.py"
    assert finding["evidence"] == ["docs/a.md:7 states `src/gone.py`"]


@pytest.mark.parametrize("fmt", ["table", "json"])
def test_a_clean_tree_still_states_what_was_not_checked(monkeypatch, tmp_path, fmt):
    """Zero findings is a real answer, and it must not read as full coverage."""
    result = _invoke(monkeypatch, tmp_path, [], ["--format", fmt])

    assert result.exit_code == 0
    if fmt == "json":
        assert json.loads(result.output)["findings_basis"] == DETECTION_BASIS
    else:
        # Rich wraps the sentence, so match a fragment that survives wrapping.
        assert "uncheckable" in result.output


@pytest.mark.parametrize(
    ("sentinel", "code"),
    [
        (doc_drift_cmd._NO_INDEX, "no_index"),
        (doc_drift_cmd._STALE_INDEX, "index_predates_doc_drift"),
    ],
)
def test_an_unreadable_index_refuses_rather_than_reporting_zero(
    monkeypatch, tmp_path, sentinel, code
):
    """The failure this guards: "no rows" and "no table" rendering identically."""
    result = _invoke(monkeypatch, tmp_path, sentinel, ["--format", "json"])

    assert result.exit_code == 1
    payload = json.loads(result.output)
    assert payload["error"] == code
    assert "findings" not in payload


def test_a_missing_drift_table_becomes_the_stale_sentinel(monkeypatch, tmp_path):
    """An index older than the drift table makes the read raise, not return [].

    Measured on a real checkout: ``repo_index_session`` shields opening the
    store, not querying it, so this is the command's to catch.
    """

    async def _raise(*_a, **_k):
        raise OperationalError("select", {}, Exception("no such table: doc_drift_findings"))

    import repowise.core.persistence.crud as crud

    monkeypatch.setattr(crud, "get_doc_drift_findings", _raise)

    class _Session:
        pass

    def _fake_session(_root):
        import contextlib

        @contextlib.asynccontextmanager
        async def _cm():
            yield (_Session(), "repo-id")

        return _cm()

    monkeypatch.setattr(doc_drift_cmd, "repo_index_session", _fake_session)

    import asyncio

    got = asyncio.run(doc_drift_cmd._read(tmp_path, min_confidence=0.4, kinds=()))
    assert got is doc_drift_cmd._STALE_INDEX


def test_findings_group_under_one_header_per_document(monkeypatch, tmp_path):
    """The store orders by confidence first, so rows for one document are not
    contiguous unless they happen to share one. Without a sort the same
    document prints a header for each run."""
    rows = [
        serialize_doc_drift_row(_Row(file_path="docs/a.md", line_number=1, confidence=0.95)),
        serialize_doc_drift_row(_Row(file_path="docs/b.md", line_number=3, confidence=0.9)),
        serialize_doc_drift_row(_Row(file_path="docs/a.md", line_number=9, confidence=0.85)),
    ]

    result = _invoke(monkeypatch, tmp_path, rows)

    # Headers are the only lines that are a bare document path; the evidence
    # lines name it too, so count headers rather than occurrences.
    headers = [line for line in result.output.splitlines() if line.strip() == "docs/a.md"]
    assert len(headers) == 1


def test_kind_and_confidence_filters_reach_the_query_and_the_rows(monkeypatch, tmp_path):
    """``--min-confidence`` is pushed into SQL; ``--kind`` filters the rows."""
    import asyncio
    import contextlib

    seen: dict = {}
    rows = [_Row(kind="path"), _Row(kind="anchor", confidence=0.95)]

    async def _fake(_session, _repo_id, **kw):
        seen.update(kw)
        return rows

    import repowise.core.persistence.crud as crud

    monkeypatch.setattr(crud, "get_doc_drift_findings", _fake)

    @contextlib.asynccontextmanager
    async def _cm(_root):
        yield (object(), "repo-id")

    monkeypatch.setattr(doc_drift_cmd, "repo_index_session", _cm)

    got = asyncio.run(doc_drift_cmd._read(tmp_path, min_confidence=0.9, kinds=("anchor",)))

    assert seen["min_confidence"] == 0.9
    assert [f["kind"] for f in got] == ["anchor"]


def test_min_confidence_defaults_to_showing_what_the_index_stored(monkeypatch, tmp_path):
    """The pass already applied the repository's cutoff at write time. A second
    hardcoded cutoff here would hide rows ``get_health`` still shows."""
    captured: dict = {}

    def _run(coro):
        coro.close()
        return []

    monkeypatch.setattr(doc_drift_cmd, "_repo_path", lambda *a, **k: tmp_path)
    monkeypatch.setattr(doc_drift_cmd, "run_async", _run)
    monkeypatch.setattr(
        doc_drift_cmd, "_payload", lambda root, findings, mc: captured.setdefault("mc", mc) or {}
    )
    monkeypatch.setattr(doc_drift_cmd, "_render", lambda _p: None)

    CliRunner().invoke(doc_drift_cmd.doc_drift_command, [])

    assert captured["mc"] is None
