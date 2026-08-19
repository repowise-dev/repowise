"""Unit tests for the in-code rationale miner (`_code_rationale`).

The miner recovers the "why" that lives in source comments — the unbiased
A/B's one durable loss (task T4): rationale in a code comment that neither
get_why's decision/git search nor get_answer's wiki retrieval can find.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from repowise.server.mcp_server._code_rationale import (
    _salient_numbers,
    extract_comment_blocks,
    grep_comment_candidates,
    mine_rationale,
)

PY_SOURCE = '''import os

# We set PYTHONIOENCODING here because the Windows console uses cp1252
# and the checkmark glyph crashes the CLI otherwise.
ENCODING = "utf-8"

def divide(a, b):
    denom = b or 1  # fall back to 1 to avoid a divide-by-zero downstream
    return a / denom

# TODO: refactor this later
LIMIT = 50

class Bar:
    """Bar exists instead of a dict because callers need attribute access."""
    pass

URL = "https://example.com/path"  # not a comment, lives in a string
'''

TS_SOURCE = """import { x } from 'y';

// We debounce at 200ms rather than 100ms because the API rate-limits
// bursts and a tighter window trips the limiter.
const DEBOUNCE = 200;

/* This block is a workaround for a Safari layout bug: flex-basis must be
   set explicitly or the panel collapses. */
function layout() {}

// plain label
const z = 1;
"""


def _write(tmp_path, name, content):
    p = tmp_path / name
    p.write_text(content, encoding="utf-8")
    return p


def test_extract_coalesces_multiline_python_comment():
    # Shared core tokenizer returns CommentBlock dataclasses; trailing inline
    # comments are recall-only (include_trailing=True), matching mine_rationale.
    blocks = extract_comment_blocks(PY_SOURCE, "py", include_trailing=True)
    texts = [b.text for b in blocks]
    # The two-line # comment becomes one block spanning lines 3-4.
    enc = next(b for b in blocks if "PYTHONIOENCODING" in b.text)
    assert enc.start_line == 3 and enc.end_line == 4
    assert "checkmark glyph crashes" in enc.text
    # Trailing inline comment recovered.
    assert any("divide-by-zero" in t for t in texts)
    # Docstring recovered.
    assert any("attribute access" in t for t in texts)


def test_extract_handles_c_style_block_and_line_comments():
    blocks = extract_comment_blocks(TS_SOURCE, "ts")
    texts = [b.text for b in blocks]
    assert any("debounce" in t.lower() and "rate-limits" in t.lower() for t in texts)
    assert any("workaround for a Safari layout bug" in t for t in texts)


def test_mine_ranks_marker_plus_query_overlap_first(tmp_path):
    _write(tmp_path, "mod.py", PY_SOURCE)
    out = mine_rationale(str(tmp_path), ["mod.py"], "why PYTHONIOENCODING windows console")
    assert out, "expected at least one rationale block"
    top = out[0]
    assert "PYTHONIOENCODING" in top["comment"]
    assert top["lines"] == [3, 4]
    assert "windows" in top["matched_terms"]


def test_mine_path_mode_returns_marker_comments_without_query(tmp_path):
    _write(tmp_path, "mod.py", PY_SOURCE)
    out = mine_rationale(str(tmp_path), ["mod.py"], None)
    comments = " ".join(r["comment"] for r in out)
    # All three rationale-marker comments surface...
    assert "PYTHONIOENCODING" in comments
    assert "divide-by-zero" in comments
    assert "attribute access" in comments
    # ...but the bare "TODO: refactor" label (no rationale marker) does not.
    assert "refactor this later" not in comments


def test_mine_drops_non_rationale_comments_under_query(tmp_path):
    _write(tmp_path, "mod.ts", TS_SOURCE)
    out = mine_rationale(str(tmp_path), ["mod.ts"], "debounce rate limit")
    assert any("debounce" in r["comment"].lower() for r in out)
    # "plain label" has neither a marker nor query overlap.
    assert all("plain label" not in r["comment"] for r in out)


def test_mine_url_in_string_is_not_mistaken_for_comment(tmp_path):
    _write(tmp_path, "mod.py", PY_SOURCE)
    out = mine_rationale(str(tmp_path), ["mod.py"], None)
    assert all("example.com" not in r["comment"] for r in out)


def test_mine_near_line_boost_prefers_comment_by_named_symbol(tmp_path):
    src = (
        "# generic note because of history\n" * 1
        + "x = 1\n" * 50
        + "# special-cased because the loader needs it early\n"
        + "def loader():\n    pass\n"
    )
    _write(tmp_path, "m.py", src)
    near = {"m.py": 52}  # the loader comment sits at ~line 52
    out = mine_rationale(str(tmp_path), ["m.py"], "loader", near_lines=near)
    assert out[0]["lines"][0] >= 50  # the near-line comment wins the tie


def test_mine_is_bounded_and_safe(tmp_path):
    # Path escape: a file outside the root is refused, not read.
    _write(tmp_path, "mod.py", PY_SOURCE)
    assert mine_rationale(str(tmp_path), ["../../etc/passwd"], None) == []
    # No repo root -> empty, no raise.
    assert mine_rationale(None, ["mod.py"], "x") == []
    # Empty file list → empty.
    assert mine_rationale(str(tmp_path), [], "x") == []


def test_mine_drops_markerless_docstrings_when_a_marker_block_wins(tmp_path):
    """Generic query terms (lines/source/one) drag in plain docstrings; once a
    real rationale-marker block is found, the marker-less ones are dropped."""
    src = (
        '"""Slice text to source lines; one indexed symbol per call."""\n'
        "MAX = 1\n"
        "# The source line cap is 600 because round-trip count, not payload\n"
        "# size, dominates agent token cost.\n"
        "LIMIT = 600\n"
    )
    p = tmp_path / "s.py"
    p.write_text(src, encoding="utf-8")
    out = mine_rationale(str(tmp_path), ["s.py"], "why source lines cap one")
    assert out, "expected the rationale block to survive"
    # The marker block (has 'because') is the only one kept.
    assert all("because" in r["comment"] for r in out)
    assert any("round-trip count" in r["comment"] for r in out)


def test_mine_keeps_term_only_blocks_when_no_marker_exists(tmp_path):
    """Recall fallback: if nothing carries a marker, strong term overlap still
    surfaces (so a marker-less but on-topic comment isn't lost)."""
    src = "# configures the source line buffer for one pass\nBUF = 1\n"
    p = tmp_path / "s.py"
    p.write_text(src, encoding="utf-8")
    out = mine_rationale(str(tmp_path), ["s.py"], "source line buffer pass")
    assert any("source line buffer" in r["comment"] for r in out)


def test_mine_caps_result_count(tmp_path):
    # Many rationale comments → result list is capped (<= _MAX_RESULTS=6).
    lines = [f"# reason {i}: we avoid path {i} because it deadlocks\n" for i in range(40)]
    _write(tmp_path, "big.py", "".join(lines))
    out = mine_rationale(str(tmp_path), ["big.py"], None)
    assert 0 < len(out) <= 6


# --- Number-aware scoring (concept anchoring, piece 1) ----------------------


def test_salient_numbers_extracts_whole_tokens():
    assert _salient_numbers("why is the caller list capped at 50") == ["50"]
    assert _salient_numbers("cap symbols at 40 not 600") == ["40", "600"]
    # No number, version-dotted ignored as one float token, no bare words.
    assert _salient_numbers("why is this limited") == []


def test_mine_number_disambiguates_two_caps(tmp_path):
    """The literal question number picks the right cap comment - the cap-40 vs
    cap-600 mis-rank the prototype surfaced: _content_terms drops '50', so
    without number scoring both 'because'-marked caps tie."""
    src = (
        "# The caller list cap is 50 because the median fan-in is 2.\n"
        "CALLER_CAP = 50\n"
        "# The source line cap is 600 because round-trip count dominates.\n"
        "SOURCE_CAP = 600\n"
    )
    _write(tmp_path, "s.py", src)
    out = mine_rationale(str(tmp_path), ["s.py"], "why is the caller list capped at 50")
    assert out
    assert "50" in out[0]["comment"] and "600" not in out[0]["comment"]
    assert "50" in out[0]["matched_terms"]


def test_mine_number_match_is_whole_token(tmp_path):
    """'40' must not match '400' / '0.40' - a substring match would re-introduce
    the very mis-rank number scoring exists to fix."""
    src = (
        "# We page in batches of 400 because the API rejects larger requests.\n"
        "BATCH = 400\n"
        "# The symbol cap is 40 because dense files flood the context window.\n"
        "SYMBOL_CAP = 40\n"
    )
    _write(tmp_path, "s.py", src)
    out = mine_rationale(str(tmp_path), ["s.py"], "why does it cap symbols at 40")
    assert out
    assert "40" in out[0]["comment"] and "400" not in out[0]["comment"]


def _git_repo(tmp_path: Path, files: dict[str, str]) -> Path:
    """Create a committed git repo so ``git grep`` (tracked-files-only) works."""
    for name, content in files.items():
        p = tmp_path / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.email=t@t.dev",
            "-c",
            "user.name=t",
            "commit",
            "-qm",
            "init",
        ],
        cwd=tmp_path,
        check=True,
    )
    return tmp_path


_CALLER_CAP_FILES = {
    "enrichment.py": (
        "def neighbors():\n"
        "    # Cap the caller list at 50 because the median fan-in is 2 and\n"
        "    # 50 covers 99.5% of symbols in a single call.\n"
        "    limit = 50\n"
    ),
    # A boilerplate call site: number, but no rationale comment-leading line.
    "pager.py": "PAGE = 50  # default page size\n",
    "other.py": "# unrelated note about caching\nX = 1\n",
}


def test_grep_comment_candidates_ranks_number_bearing_file(tmp_path):
    repo = _git_repo(tmp_path, _CALLER_CAP_FILES)
    cands = grep_comment_candidates(str(repo), "why is the caller list capped at 50")
    assert cands, "expected at least one grep candidate"
    # The file whose COMMENT explains the 50 wins; the boilerplate call site
    # (trailing comment, not comment-leading) is not matched.
    assert cands[0] == "enrichment.py"
    assert "pager.py" not in cands


def test_grep_comment_candidates_skips_gitignored_file(tmp_path):
    """A gitignored path must not be returned as a rationale candidate.

    ``git grep`` scans tracked files, but a file force-added before it was
    gitignored stays tracked (this is exactly how a stale vendored copy leaks
    in). The compiled exclude spec must drop it while keeping the real file.
    """
    _RATIONALE = (
        "# The widget list cap is 77 because the median fan-in is small\n"
        "# and 77 covers nearly every widget in one call.\n"
        "LIMIT = 77\n"
    )
    (tmp_path / ".gitignore").write_text("stale/\n", encoding="utf-8")
    (tmp_path / "enrichment.py").write_text(_RATIONALE, encoding="utf-8")
    (tmp_path / "stale").mkdir()
    (tmp_path / "stale" / "leak.py").write_text(_RATIONALE, encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "add", "enrichment.py", ".gitignore"], cwd=tmp_path, check=True)
    # Force-track the ignored copy so git grep can still see it.
    subprocess.run(["git", "add", "-f", "stale/leak.py"], cwd=tmp_path, check=True)
    subprocess.run(
        ["git", "-c", "user.email=t@t.dev", "-c", "user.name=t", "commit", "-qm", "init"],
        cwd=tmp_path,
        check=True,
    )
    cands = grep_comment_candidates(str(tmp_path), "why is the widget list capped at 77")
    assert "enrichment.py" in cands
    assert "stale/leak.py" not in cands


def test_grep_comment_candidates_needs_a_noun(tmp_path):
    # Numbers but no content noun (all stopwords) -> nothing to anchor on.
    repo = _git_repo(tmp_path, _CALLER_CAP_FILES)
    assert grep_comment_candidates(str(repo), "why is it at 50") == []
    # No repo root -> empty, no raise.
    assert grep_comment_candidates(None, "why is the caller list capped at 50") == []


# --- Concept anchoring hit injection (piece 3) ------------------------------


async def test_concept_anchor_injects_winner_as_dominant(tmp_path):
    from repowise.server.mcp_server.tool_answer.symbols import _concept_anchor_hits

    repo = _git_repo(tmp_path, _CALLER_CAP_FILES)
    # Retrieval missed enrichment.py - it led with the boilerplate pager file.
    hits = [
        {"target_path": "pager.py", "page_type": "file_page", "score": 2.0},
        {"target_path": "other.py", "page_type": "file_page", "score": 1.0},
    ]
    out = await _concept_anchor_hits(
        Path(str(repo)), "why is the caller list capped at 50", hits
    )
    assert out[0]["target_path"] == "enrichment.py"
    assert out[0]["_concept_anchored"] is True
    # Dominates the prior top so the dominance gate passes.
    assert out[0]["score"] > 2.0
    # The mined comment + its line are stashed for code_rationale surfacing.
    assert out[0]["_concept_near_line"] >= 1
    assert "50" in out[0]["_concept_rationale"]["comment"]


async def test_concept_anchor_skips_without_a_number(tmp_path):
    from repowise.server.mcp_server.tool_answer.symbols import _concept_anchor_hits

    repo = _git_repo(tmp_path, _CALLER_CAP_FILES)
    hits = [{"target_path": "pager.py", "page_type": "file_page", "score": 2.0}]
    out = await _concept_anchor_hits(
        Path(str(repo)), "why is the caller list capped", hits
    )
    assert all(not h.get("_concept_anchored") for h in out)


async def test_concept_anchor_skips_when_retrieval_already_led_with_winner(tmp_path):
    from repowise.server.mcp_server.tool_answer.symbols import _concept_anchor_hits

    repo = _git_repo(tmp_path, _CALLER_CAP_FILES)
    # Retrieval already ranked enrichment.py top - leave the confidence label to
    # the existing machinery; do not force it past the dominance gate.
    hits = [
        {"target_path": "enrichment.py", "page_type": "file_page", "score": 2.0},
        {"target_path": "other.py", "page_type": "file_page", "score": 1.95},
    ]
    out = await _concept_anchor_hits(
        Path(str(repo)), "why is the caller list capped at 50", hits
    )
    assert all(not h.get("_concept_anchored") for h in out)
