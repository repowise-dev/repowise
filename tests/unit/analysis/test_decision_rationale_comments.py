"""In-code rationale harvest — the shared miner + the index-time extractor pass.

Source 8 (``code_comment``) mines rationale-bearing source comments into
low-confidence ``proposed`` decision records so the architectural intent that
lives in comments (not ADRs) enters the queryable corpus. These tests cover the
precision guardrails (the whole risk) and the extractor wiring.
"""

from __future__ import annotations

from types import SimpleNamespace

from repowise.core.analysis.decision_extractor import DecisionExtractor
from repowise.core.analysis.decisions.rationale_comments import (
    extract_comment_blocks,
    harvest_file_rationale,
    has_causal_marker,
    has_rationale_marker,
    is_license_or_boilerplate,
    looks_like_commented_out_code,
    marker_strength,
)

# A real rationale comment: prose, a causal marker, enclosing a function.
_RATIONALE_PY = '''\
"""Module docstring."""

import time


def call_api():
    # We retry on 429 here rather than in the client because the client is
    # shared across tenants and a global backoff would starve everyone.
    return time.time()
'''

_LICENSE_PY = """\
# Copyright 2026 Acme Inc. All rights reserved.
# Licensed under the Apache License because the board said so.
import os
"""


# ---------------------------------------------------------------------------
# Comment tokenizer
# ---------------------------------------------------------------------------


def test_extract_comment_blocks_coalesces_consecutive_lines():
    blocks = extract_comment_blocks(_RATIONALE_PY, "py")
    # The two-line rationale run coalesces into one block.
    runs = [b for b in blocks if "we retry on 429" in b.text.lower()]
    assert len(runs) == 1
    b = runs[0]
    assert b.start_line < b.end_line  # spans both comment lines
    assert "starve everyone" in b.text


def test_extract_comment_blocks_handles_c_style_and_returns_empty_for_unknown():
    js = "/* we batch writes because the disk fsync dominates latency */\nx = 1;\n"
    blocks = extract_comment_blocks(js, "js")
    assert any("batch writes" in b.text for b in blocks)
    # Unknown / non-code extension yields nothing.
    assert extract_comment_blocks("plain prose because yes", "txt") == []


# ---------------------------------------------------------------------------
# Guardrails
# ---------------------------------------------------------------------------


def test_marker_detection_and_strength():
    assert has_rationale_marker("we do this because of X")
    assert not has_rationale_marker("just a plain label here")
    # Two distinct markers present → strength 2.
    assert marker_strength("because we must avoid the deadlock") >= 2


def test_causal_marker_is_stricter_than_rationale_marker():
    # An intent label has a rationale marker but no causal reason.
    assert has_rationale_marker("NOTE: always flush before close")
    assert not has_causal_marker("NOTE: always flush before close")
    # A stated reason is causal.
    assert has_causal_marker("we batch writes because fsync dominates")


def test_license_and_preamble_detected():
    assert is_license_or_boilerplate("Copyright 2026 Acme Inc. All rights reserved.")
    assert is_license_or_boilerplate("SPDX-License-Identifier: MIT")
    assert is_license_or_boilerplate("-*- coding: utf-8 -*-")
    assert not is_license_or_boilerplate("we cache this because it is hot")


def test_commented_out_code_detected_but_prose_kept():
    assert looks_like_commented_out_code(("x = compute(y)", "return x"))
    assert not looks_like_commented_out_code(
        ("we avoid the global lock because it serializes every tenant",)
    )


# ---------------------------------------------------------------------------
# Per-file harvest
# ---------------------------------------------------------------------------


def test_harvest_keeps_rationale_drops_license():
    kept = harvest_file_rationale(_RATIONALE_PY, "py")
    assert len(kept) == 1
    assert "starve everyone" in kept[0].text

    # The license block carries "because" but is boilerplate → dropped.
    assert harvest_file_rationale(_LICENSE_PY, "py") == []


def test_harvest_drops_thin_and_markerless_comments():
    src = "# hi\n# this is a plain comment with no causal marker at all here\nx = 1\n"
    assert harvest_file_rationale(src, "py") == []


def test_harvest_respects_per_file_cap():
    parts = []
    for i in range(6):
        parts.append(f"def f{i}():")
        parts.append(f"    # branch {i} exists because edge case {i} would otherwise crash")
        parts.append(f"    return {i}")
    src = "\n".join(parts) + "\n"
    kept = harvest_file_rationale(src, "py", max_per_file=3)
    assert len(kept) == 3


def test_harvest_non_code_extension_returns_empty():
    assert harvest_file_rationale("we did this because reasons", "md") == []


def test_harvest_excludes_docstrings_by_default_but_keeps_line_comments():
    src = (
        '"""We pick gRPC because REST added latency on the hot path."""\n'
        "# we cache the token because the lookup is expensive\n"
        "x = 1\n"
    )
    # Default: docstring rationale is skipped, the line comment is kept.
    kept = harvest_file_rationale(src, "py")
    assert len(kept) == 1
    assert "cache the token" in kept[0].text
    # Opt in to docstrings (the MCP recall mode) → both surface.
    both = harvest_file_rationale(src, "py", include_docstrings=True)
    assert len(both) == 2


def test_harvest_splits_and_drops_separator_rules():
    src = (
        "# ----------------------------------------------------------------\n"
        "# Section banner only\n"
        "# ----------------------------------------------------------------\n"
        "# we skip the cache here because the entry is already stale\n"
        "y = 2\n"
    )
    kept = harvest_file_rationale(src, "py")
    assert len(kept) == 1
    text = kept[0].text
    assert "skip the cache" in text
    assert "---" not in text  # divider rule never glued in
    assert "Section banner" not in text  # markerless run dropped


def test_require_causal_false_admits_intent_labels():
    src = "# NOTE: always flush the buffer before closing the handle here\nz = 3\n"
    assert harvest_file_rationale(src, "py") == []  # no causal reason
    loose = harvest_file_rationale(src, "py", require_causal=False)
    assert len(loose) == 1


# ---------------------------------------------------------------------------
# Extractor pass (Source 8)
# ---------------------------------------------------------------------------


async def test_harvest_rationale_comments_emits_grounded_proposed_record(tmp_path):
    (tmp_path / "client.py").write_text(_RATIONALE_PY, encoding="utf-8")

    ex = DecisionExtractor(repo_path=tmp_path)  # deterministic, no provider
    decisions = await ex.harvest_rationale_comments()

    assert len(decisions) == 1
    d = decisions[0]
    assert d.source == "code_comment"
    assert d.status == "proposed"
    assert d.confidence < 0.5  # ranks below real ADRs / inline markers
    # Verbatim grounding: decision + source_quote are the comment itself.
    assert "starve everyone" in d.decision
    assert d.source_quote == d.decision
    assert d.evidence_file == "client.py"
    assert d.affected_files == ["client.py"]
    assert d.title  # never title-only-empty; title is the comment's first line


async def test_harvest_resolves_enclosing_symbol_from_parsed_files(tmp_path):
    (tmp_path / "client.py").write_text(_RATIONALE_PY, encoding="utf-8")

    # The rationale comment sits inside call_api() (lines ~7-9). Provide a
    # parsed-file with that symbol so the harvest can attribute it.
    parsed = SimpleNamespace(
        file_info=SimpleNamespace(path="client.py"),
        symbols=[
            SimpleNamespace(
                start_line=6, end_line=10, name="call_api", qualified_name="client.call_api"
            )
        ],
    )
    ex = DecisionExtractor(repo_path=tmp_path, parsed_files=[parsed])
    decisions = await ex.harvest_rationale_comments()

    assert len(decisions) == 1
    assert "client.call_api" in decisions[0].context


async def test_code_comment_records_pass_the_gate_in_extract_all(tmp_path):
    (tmp_path / "client.py").write_text(_RATIONALE_PY, encoding="utf-8")

    ex = DecisionExtractor(repo_path=tmp_path)
    report = await ex.extract_all()

    assert report.by_source["code_comment"] == 1
    cc = [d for d in report.decisions if d.source == "code_comment"]
    assert len(cc) == 1
    # Verbatim comment → the substring gate stamps it exact, never rejects it.
    assert cc[0].verification == "exact"


async def test_harvest_skips_untracked_files_in_a_git_repo(tmp_path):
    """In a git checkout the harvest scopes to tracked files: untracked /
    excluded working dirs (local-stash, scratch dumps) must not produce records.
    Gitless repos fall back to the full walk (the tmp_path tests above)."""
    import subprocess

    def _git(*args: str) -> None:
        subprocess.run(
            ["git", "-C", str(tmp_path), *args],
            check=True,
            capture_output=True,
        )

    _git("init")
    _git("config", "user.email", "t@t.t")
    _git("config", "user.name", "t")

    (tmp_path / "tracked.py").write_text(_RATIONALE_PY, encoding="utf-8")
    _git("add", "tracked.py")
    _git("commit", "-m", "add tracked")

    # An untracked file carrying a textbook rationale comment.
    (tmp_path / "untracked.py").write_text(
        "def f():\n"
        "    # We special-case this because the upstream API double-encodes.\n"
        "    return 1\n",
        encoding="utf-8",
    )

    ex = DecisionExtractor(repo_path=tmp_path)
    decisions = await ex.harvest_rationale_comments()

    files = {d.evidence_file for d in decisions}
    assert "tracked.py" in files
    assert "untracked.py" not in files
