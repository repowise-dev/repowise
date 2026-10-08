"""Low and degraded answers serve slim best_guesses rows instead of page excerpts."""

from __future__ import annotations

import copy
import inspect
import json
import os

import pytest

from repowise.server.mcp_server.tool_answer.payload import build_abstain_payload
from repowise.server.mcp_server.tool_answer.projection import (
    _EXCERPT_NOTE,
    _add_file_sizes,
    _file_size,
    project_answer_payload,
)
from repowise.server.mcp_server.tool_answer.retrieval import serialize_candidate_file_facts

_EXCERPT = "Page prose. " * 125  # about the 1,500-char excerpt a real guess carries
_POOL = [f"src/pkg/f{i}.py" for i in range(10)]


def _raw(confidence: str, **extra) -> dict:
    raw = {
        "answer": "Probably in f0.",
        "citations": ["src/pkg/f1.py"],
        "confidence": confidence,
        "retrieval_quality": "weak",
        "fallback_targets": _POOL[:3],
        "retrieval": [{"path": p, "excerpt": _EXCERPT, "score": 1.0} for p in _POOL[:5]],
        "best_guesses": [
            {"file": p, "why_relevant": f"guess {p}", "score": 3.0 - i, "excerpt": _EXCERPT}
            for i, p in enumerate(_POOL[:3])
        ],
        "candidate_files": list(_POOL),
        "_candidate_file_facts": {
            p: {
                "why": f"Implements function run_{i}.",
                "functions": [{"name": f"run_{i}", "line": 10 + i}],
            }
            for i, p in enumerate(_POOL)
        },
        "next_action_hint": "Start from src/pkg/f0.py, it ranked highest.",
        "note": "Each best_guess entry names why that file is in the running.",
        "_meta": {"contract_version": 1},
    }
    raw.update(extra)
    return raw


@pytest.mark.parametrize(
    ("confidence", "extra"), [("low", {}), ("medium", {"degraded": "no-llm-provider"})]
)
def test_low_and_degraded_slim_the_guesses(confidence, extra):
    out = project_answer_payload(_raw(confidence, **extra), question="where is run handled")

    assert "excerpt" not in json.dumps(out)
    assert [g["file"] for g in out["best_guesses"]] == _POOL[:3]
    # Existing keys stay as they were; the facts only add what is missing.
    assert out["best_guesses"][0] == {
        "file": "src/pkg/f0.py",
        "why_relevant": "guess src/pkg/f0.py",
        "score": 3.0,
        "functions": [{"name": "run_0", "line": 10}],
    }
    # candidate_files keeps its bare-path shape at every grade.
    assert out["candidate_files"] == [p for p in _POOL if p != "src/pkg/f1.py"][:5]
    assert "_candidate_file_facts" not in out


def test_a_guess_without_a_reason_takes_the_facts_reason():
    raw = _raw("low")
    raw["best_guesses"][0].pop("why_relevant")
    out = project_answer_payload(raw, question="q")
    assert out["best_guesses"][0]["why"] == "Implements function run_0."


def test_the_abstain_prose_stops_pointing_at_an_excerpt():
    raw = _raw(
        "low",
        note="Each best_guess entry names why that file is in the running, and its "
        "excerpt carries that page's actual content.",
        next_action_hint="Start from the excerpt of src/pkg/f0.py, it scored highest.",
    )
    out = project_answer_payload(raw, question="q")
    assert out["note"] == "Each best_guess entry names why that file is in the running."
    assert "excerpt" not in out["next_action_hint"]
    assert out["next_action_hint"].startswith("Read src/pkg/f0.py first")


def test_the_retargeted_prose_still_matches_the_abstain_source():
    """The rewrite keys on literal abstain wording; a reword there must fail here."""
    source = inspect.getsource(build_abstain_payload)
    clause = ", and its excerpt carries that page's actual content."
    assert clause in source
    assert _EXCERPT_NOTE.search(clause)
    assert 'f"Start from the excerpt of ' in source


def test_the_hint_names_the_first_guess_with_a_path():
    raw = _raw("low", next_action_hint="Start from the excerpt of x, it scored highest.")
    raw["best_guesses"].insert(0, {"why_relevant": "no file", "excerpt": "e"})
    out = project_answer_payload(raw, question="q")
    assert out["next_action_hint"].startswith("Read src/pkg/f0.py first")


@pytest.mark.parametrize("confidence", ["high", "medium"])
def test_medium_and_high_bytes_do_not_change(confidence):
    with_facts = project_answer_payload(_raw(confidence), question="q")
    raw = _raw(confidence)
    raw.pop("_candidate_file_facts")
    without = project_answer_payload(raw, question="q")

    assert json.dumps(with_facts, sort_keys=True) == json.dumps(without, sort_keys=True)


def test_evidence_include_still_serves_guess_excerpts_on_low():
    raw = _raw("low")
    without = copy.deepcopy(raw)
    without.pop("_candidate_file_facts")
    out = project_answer_payload(raw, question="q", include=["evidence"])

    assert all(g["excerpt"] == _EXCERPT for g in out["best_guesses"])
    assert out == project_answer_payload(without, question="q", include=["evidence"])


def test_slim_low_answer_is_much_smaller():
    raw = _raw("low")
    compact = project_answer_payload(copy.deepcopy(raw), question="q")
    expanded = project_answer_payload(raw, question="q", include=["evidence"])
    assert len(json.dumps(compact)) * 3 < len(json.dumps(expanded))


def test_file_sizes_are_stamped_live_and_a_large_top_file_cues_a_ranged_read(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "big.py").write_bytes(b"x = 1\n" * 8000)
    (tmp_path / "src" / "small.py").write_bytes(b"a\nb")
    payload = {
        "best_guesses": [{"file": "src/big.py"}, {"file": "src/small.py"}, {"file": "gone.py"}],
        "next_action_hint": "Start from src/big.py.",
    }

    _add_file_sizes(payload, tmp_path)

    big, small, gone = payload["best_guesses"]
    assert (big["lines"], big["size_bytes"]) == (8000, 48000)
    assert (small["lines"], small["size_bytes"]) == (2, 3)
    assert gone == {"file": "gone.py"}
    assert payload["next_action_hint"].startswith("Start from src/big.py. src/big.py is 46 KB")
    assert 'include=["skeleton"]' in payload["next_action_hint"]

    payload = {"best_guesses": [{"file": "src/small.py"}], "next_action_hint": "h"}
    _add_file_sizes(payload, tmp_path)
    assert payload["next_action_hint"] == "h"


def test_the_size_cue_uses_the_first_guess_with_a_path(tmp_path):
    (tmp_path / "big.py").write_bytes(b"x\n" * 30000)
    payload = {"best_guesses": [{"why_relevant": "no file"}, {"file": "big.py"}]}
    _add_file_sizes(payload, tmp_path)
    assert payload["best_guesses"][0] == {"why_relevant": "no file"}
    assert payload["next_action_hint"].startswith("big.py is 58 KB")


def test_a_huge_file_reports_its_size_but_is_never_read_for_lines(tmp_path):
    with (tmp_path / "huge.bin").open("wb") as handle:
        handle.truncate(3_000_000)
    assert _file_size(tmp_path, "huge.bin") == (None, 3_000_000)
    assert _file_size(tmp_path, "missing.py") is None
    (tmp_path / "dir").mkdir()
    assert _file_size(tmp_path, "dir") is None


def test_file_sizes_refuse_paths_outside_the_repo(tmp_path):
    (tmp_path / "outside.py").write_text("secret\n")
    root = tmp_path / "repo"
    root.mkdir()
    payload = {"best_guesses": [{"file": "../outside.py"}]}
    _add_file_sizes(payload, root)
    assert payload["best_guesses"] == [{"file": "../outside.py"}]


def test_file_sizes_refuse_a_symlink_that_escapes_the_repo(tmp_path):
    (tmp_path / "outside.py").write_text("secret\n")
    root = tmp_path / "repo"
    root.mkdir()
    try:
        os.symlink(tmp_path / "outside.py", root / "link.py")
    except (OSError, NotImplementedError):
        pytest.skip("symlinks are not available here")
    assert _file_size(root, "link.py") is None


def test_candidate_file_facts_come_from_the_resolved_hits():
    hits = [
        {
            "target_path": "src/a.py",
            "page_type": "file_page",
            "score": 2.34567,
            "symbols": [{"name": "handle", "kind": "function", "start_line": 40, "_matched": True}],
            "_defines": [("handle", 40), ("Router", 5), ("route", 70), ("extra", 90)],
        },
        {"target_path": "src/a.py", "page_type": "file_page", "score": 1.0},
        {"target_path": "src/b.py", "page_type": "file_page", "summary": "Routes requests. More."},
        {"target_path": "src/c.py", "page_type": "file_page", "score": 0.5},
    ]

    facts = serialize_candidate_file_facts(hits)

    # c.py has nothing to say, so it gets no entry.
    assert list(facts) == ["src/a.py", "src/b.py"]
    assert facts["src/a.py"] == {
        "why": "Implements function handle.",
        "functions": [
            {"name": "handle", "line": 40},
            {"name": "Router", "line": 5},
            {"name": "route", "line": 70},
        ],
    }
    assert facts["src/b.py"] == {"why": "Routes requests."}


@pytest.mark.asyncio
async def test_candidate_file_facts_never_leak_through_the_tool_on_a_cache_hit(
    setup_mcp, monkeypatch
):
    import repowise.server.mcp_server.tool_answer.answer as answer_mod
    from repowise.server.mcp_server import get_answer, tool_middleware

    from .test_answer_projection import _patch_retrieval, _Provider

    _patch_retrieval(monkeypatch, answer_mod)
    provider = _Provider("Authentication is implemented in src/auth/service.py.")
    monkeypatch.setattr(answer_mod, "_resolve_provider_for_answer", lambda _path: provider)
    call = tool_middleware(get_answer)

    fresh = await call("where is the leak-free authentication implemented")
    cached = await call("where is the leak-free authentication implemented")

    assert cached["_meta"]["cached"] is True
    assert provider.calls == 1
    for reply in (fresh, cached):
        assert "_candidate_file_facts" not in json.dumps(reply, default=str)
