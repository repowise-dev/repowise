"""Data-shape grounding: answer "what fields does X contain" from source.

A data-shape question names a data blob and asks for its field set. Instead of
gating to a best_guesses pointer list (which triggers the agent's Read/get_symbol
drill), the tool mines the field set straight from source: a documented ``{...}``
shape near the identifier (authoritative -> high) or the concrete keys consumers
pull off the parsed value (usage-mined -> medium).

These tests use synthetic repos with varied identifiers and shapes so nothing is
tuned to any one real question. The precision contract is the load-bearing part:
every reported field must be a quoted token from source, and an identifier with
no groundable shape must fall through (return None), never a fabricated field.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from repowise.server.mcp_server.tool_answer.data_shape import (
    _grep_identifier_files,
    _is_data_shape_question,
    mine_data_shape,
)
from repowise.core.exclusion import build_exclude_spec


def _write(root: Path, rel: str, body: str) -> None:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body, encoding="utf-8")


# --- Question detection ---------------------------------------------------


@pytest.mark.parametrize(
    "question,ids,expected",
    [
        (
            "What fields does each entry in co_change_partners_json contain?",
            {"co_change_partners_json"},
            True,
        ),
        ("what keys are in the blame_record blob", {"blame_record"}, True),
        ("what columns does the health_metric_row have", {"health_metric_row"}, True),
        ("describe the schema of GitCommitMeta", {"GitCommitMeta"}, True),
        ("what does each record in owner_stats consist of", {"owner_stats"}, True),
        # Mechanism questions carry no shape cue -> not data-shape.
        ("how does co_change_partners_json get populated", {"co_change_partners_json"}, False),
        ("where is the coupling graph assembled", set(), False),
        # A shape cue with no named identifier can't ground -> not data-shape.
        ("what fields are typical in a config", set(), False),
    ],
)
def test_detection(question, ids, expected):
    assert _is_data_shape_question(question, ids) is expected


# --- Documented shape (authoritative -> high) -----------------------------


def test_docstring_brace_shape_high_confidence(tmp_path):
    """A {...} field shape in a docstring near the identifier grounds high."""
    _write(
        tmp_path,
        "pkg/models.py",
        '''\
class GitMeta:
    """Row model.

    ``blame_record_json`` is the raw column: a JSON list of
    ``{"author", "commit_sha", "line_count"}`` blame records.
    """

    blame_record_json: str
''',
    )
    out = mine_data_shape(tmp_path, {"blame_record_json"})
    assert out is not None
    assert out["grounding"] == "docstring"
    assert out["confidence"] == "high"
    assert out["fields"] == ["author", "commit_sha", "line_count"]
    assert out["sources"][0]["file"] == "pkg/models.py"
    assert out["sources"][0]["kind"] == "docstring"


def test_comment_brace_shape(tmp_path):
    """A shape documented in a line comment also grounds (not just docstrings)."""
    _write(
        tmp_path,
        "pkg/store.py",
        """\
def load(raw):
    # owner_stats rows look like {"owner", "files_touched", "last_seen"}
    return parse(raw)
""",
    )
    out = mine_data_shape(tmp_path, {"owner_stats"})
    assert out is not None
    assert out["grounding"] == "docstring"
    assert set(out["fields"]) == {"owner", "files_touched", "last_seen"}


def test_executable_dict_literal_is_not_authoritative(tmp_path):
    """A bare dict literal in code (no doc context) is not a documented shape.

    It has quoted keys, but it's one construction site, not a declared schema.
    The access-mining path may still pick up keys, but the doc path must not
    mislabel an executable literal as authoritative/high.
    """
    _write(
        tmp_path,
        "pkg/build.py",
        """\
def build(widget_payload, count):
    result = {"kind": widget_payload, "n": count, "ok": True}
    return result
""",
    )
    out = mine_data_shape(tmp_path, {"widget_payload"})
    # Nothing is documented and the identifier is a scalar arg (no key access),
    # so this must fall through rather than claim {kind,n,ok} as its shape.
    assert out is None


# --- Access-mined shape (usage -> medium) ---------------------------------


def test_access_mined_shape_medium_when_no_doc(tmp_path):
    """With no documented shape, consistent key accesses ground at medium."""
    _write(
        tmp_path,
        "pkg/consume.py",
        """\
def summarize(partner_records):
    for entry in iter_records(partner_records):
        name = entry.get("file_path")
        weight = entry["co_change_count"]
        stamp = entry.get("last_seen")
    return name, weight, stamp
""",
    )
    out = mine_data_shape(tmp_path, {"partner_records"})
    assert out is not None
    assert out["grounding"] == "access"
    assert out["confidence"] == "medium"
    assert set(out["fields"]) == {"file_path", "co_change_count", "last_seen"}


def test_direct_subscript_on_identifier(tmp_path):
    """When the identifier IS the dict variable, direct key access grounds it."""
    _write(
        tmp_path,
        "pkg/direct.py",
        """\
def read(config_blob):
    host = config_blob["host"]
    port = config_blob.get("port")
    return host, port
""",
    )
    out = mine_data_shape(tmp_path, {"config_blob"})
    assert out is not None
    assert out["grounding"] == "access"
    assert set(out["fields"]) == {"host", "port"}


# --- Precision guards -----------------------------------------------------


def test_no_shape_returns_none(tmp_path):
    """An identifier that names no blob shape falls through (no fabrication)."""
    _write(tmp_path, "pkg/plain.py", "def process_records(x):\n    return x + 1\n")
    assert mine_data_shape(tmp_path, {"process_records"}) is None


def test_single_field_is_too_weak(tmp_path):
    """A one-field shape is below the min-fields floor -> abstain."""
    _write(
        tmp_path,
        "pkg/thin.py",
        """\
def read(thin_blob):
    # thin_blob entries are {"only_field"}
    return thin_blob
""",
    )
    assert mine_data_shape(tmp_path, {"thin_blob"}) is None


def test_generic_short_identifier_skipped(tmp_path):
    """Short/generic identifiers are not specific enough to ground on."""
    _write(
        tmp_path,
        "pkg/rows.py",
        """\
def go(row):
    a = row.get("x")
    b = row.get("y")
    return a, b
""",
    )
    # "row" is below the specificity floor (too short, no underscore/camel).
    assert mine_data_shape(tmp_path, {"row"}) is None


def test_absent_identifier_returns_none(tmp_path):
    """An identifier that appears nowhere in source grounds nothing."""
    _write(tmp_path, "pkg/a.py", "x = 1\n")
    assert mine_data_shape(tmp_path, {"nonexistent_blob_xyz"}) is None


def test_doc_beats_access(tmp_path):
    """When both a doc shape and accesses exist, the doc shape wins (high)."""
    _write(
        tmp_path,
        "pkg/both.py",
        '''\
class M:
    """``event_payload_json`` is a list of
    ``{"kind", "ts", "actor"}`` event records."""

    event_payload_json: str


def consume(event_payload_json):
    for e in parse(event_payload_json):
        k = e.get("kind")
        t = e.get("ts")
    return k, t
''',
    )
    out = mine_data_shape(tmp_path, {"event_payload_json"})
    assert out is not None
    assert out["grounding"] == "docstring"
    assert out["confidence"] == "high"
    assert out["fields"] == ["kind", "ts", "actor"]


# --- Alias divergence (doc omits a key consumers read as a fallback) -------


def test_documented_shape_surfaces_alias_fallback(tmp_path):
    """A doc shape that omits an ``or``-fallback alias must surface the alias.

    The docstring is authoritative for what it declares, but if a consumer reads
    ``x.get("weight_json") or x.get("weight")`` the ``weight`` alias is a real key
    the tool must not hide behind "no verification needed".
    """
    _write(
        tmp_path,
        "pkg/models.py",
        '''\
class M:
    """``metric_rows_json`` is a JSON list of
    ``{"file_path", "weight_json", "ts"}`` metric records."""

    metric_rows_json: str
''',
    )
    _write(
        tmp_path,
        "pkg/consume.py",
        """\
def use(metric_rows_json):
    for row in parse(metric_rows_json):
        w = row.get("weight_json") or row.get("weight") or 0
    return w
""",
    )
    out = mine_data_shape(tmp_path, {"metric_rows_json"})
    assert out is not None
    assert out["grounding"] == "docstring"
    assert out["fields"] == ["file_path", "weight_json", "ts"]
    aliases = {a["field"] for a in out.get("also_accessed", [])}
    assert aliases == {"weight"}
    assert out["also_accessed"][0]["file"] == "pkg/consume.py"


def test_no_alias_key_when_none_diverges(tmp_path):
    """A clean documented shape with no fallback alias carries no also_accessed."""
    _write(
        tmp_path,
        "pkg/clean.py",
        '''\
class M:
    """``clean_rows_json`` is a JSON list of
    ``{"file_path", "score", "ts"}`` records."""

    clean_rows_json: str


def use(clean_rows_json):
    for r in parse(clean_rows_json):
        s = r.get("score")
    return s
''',
    )
    out = mine_data_shape(tmp_path, {"clean_rows_json"})
    assert out is not None
    assert "also_accessed" not in out


def test_alias_precision_ignores_cross_record_comention(tmp_path):
    """A documented key co-mentioned on a different record is NOT an alias.

    ``meta["other_count"] = src[meta["file_path"]]`` reads a documented field
    (``file_path``) and another key on the same line, but it's an assignment on an
    unrelated record, not an ``or`` fallback - it must not be reported as an alias.
    """
    _write(
        tmp_path,
        "pkg/models.py",
        '''\
class M:
    """``rows_json`` is a JSON list of
    ``{"file_path", "amount", "ts"}`` records."""

    rows_json: str
''',
    )
    _write(
        tmp_path,
        "pkg/enrich.py",
        """\
def enrich(rows_json, meta, src):
    for row in parse(rows_json):
        amount = row.get("amount")
    meta["other_count"] = src[meta["file_path"]]
    return amount, meta
""",
    )
    out = mine_data_shape(tmp_path, {"rows_json"})
    assert out is not None
    assert "also_accessed" not in out


def test_none_repo_root_is_safe():
    assert mine_data_shape(None, {"anything_json"}) is None


# --- Gitignore / exclude_patterns are honoured (live-grep fallback) --------


def test_grep_skips_gitignored_file(tmp_path):
    """The ``--no-index`` filesystem grep must not return a gitignored path.

    ``tmp_path`` is not a git checkout, so ``_grep_identifier_files`` retries
    with ``git grep --no-index`` (which ignores ``.gitignore``). The compiled
    exclude spec must drop the ignored hit while keeping the tracked one.
    """
    _write(tmp_path, ".gitignore", "ignored/\n")
    _write(tmp_path, "ignored/leak.py", "x = leak_record_json\n")
    _write(tmp_path, "pkg/real.py", "y = leak_record_json\n")
    spec = build_exclude_spec(tmp_path)
    files = _grep_identifier_files(tmp_path, "leak_record_json", spec)
    assert "pkg/real.py" in files
    assert "ignored/leak.py" not in files


def test_mine_data_shape_ignores_gitignored_leak(tmp_path):
    """A gitignored stale copy must never be the shape source served."""
    _write(tmp_path, ".gitignore", "ignored/\n")
    # The gitignored copy documents a stale/wrong shape.
    _write(
        tmp_path,
        "ignored/leak.py",
        '''\
class Stale:
    """``leak_record_json`` is a JSON list of
    ``{"stale_one", "stale_two", "stale_three"}`` records."""

    leak_record_json: str
''',
    )
    # The tracked file documents the real shape.
    _write(
        tmp_path,
        "pkg/models.py",
        '''\
class Real:
    """``leak_record_json`` is a JSON list of
    ``{"author", "commit_sha", "line_count"}`` records."""

    leak_record_json: str
''',
    )
    out = mine_data_shape(tmp_path, {"leak_record_json"})
    assert out is not None
    assert out["fields"] == ["author", "commit_sha", "line_count"]
    # No served source may point at the gitignored copy.
    assert all("ignored/leak.py" != s["file"] for s in out["sources"])
