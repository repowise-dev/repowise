"""A keyless get_answer bounds its symbol_bodies source; keyed and small replies keep theirs."""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from repowise.server.mcp_server.tool_answer import projection
from repowise.server.mcp_server.tool_answer.projection import project_answer_payload

_BUDGET = projection._KEYLESS_BODY_CHARS

# (path, name, head line, filler line pattern, a definition inside the body)
_SHAPES = [
    ("pkg/codec.py", "Codec", "class Codec:", "    TABLE_{i} = 0x{i:08x}  # table row", "    def decode(self, data):"),
    ("src/router.ts", "Router", "export class Router {", "  private r{i}: number = {i}; // route slot", "  resolve(path: string) {"),
    ("Lib/Huffman.cs", "Huffman", "internal static class Huffman {", "    0b1111_{i:04}, // code", "    public static int Decode(byte[] src) {"),
]


def _write_body(root: Path, path: str, head: str, filler: str, inner: str) -> tuple[str, int]:
    """A 120-line body (head, filler, an inner definition at line 100) inside a 200-line file."""
    lines = [head]
    for i in range(1, 199):
        lines.append(inner if i == 99 else filler.format(i=i))
    lines.append("}")
    target = root / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return "\n".join(lines[:120]), len(lines)


def _raw(root: Path, *, degraded: str | None = "no-llm-provider", top: int = 1) -> dict:
    bodies = []
    # The top file's body comes second, so ordering by rank has work to do.
    others = [shape for i, shape in enumerate(_SHAPES) if i != top]
    for path, name, head, filler, inner in [others[0], _SHAPES[top], *others[1:]]:
        source, end = _write_body(root, path, head, filler, inner)
        bodies.append(
            {
                "path": path,
                "name": name,
                "lines": [1, 120],
                "source": source,
                "verified": True,
                "truncated": True,
                "continuation": f"{path}:121-{end}",
            }
        )
    raw = {
        "answer": "evidence below",
        "citations": [row["path"] for row in bodies],
        "confidence": "medium",
        "retrieval_quality": "high",
        "best_guesses": [{"file": _SHAPES[top][0], "why_relevant": "named"}],
        "symbol_bodies": bodies,
        "_meta": {},
    }
    if degraded:
        raw["degraded"] = degraded
    return raw


def test_many_large_bodies_are_bounded_with_the_top_file_first(tmp_path: Path) -> None:
    out = project_answer_payload(
        _raw(tmp_path, top=2), question="which file decodes", repo_root=tmp_path
    )
    bodies = out["symbol_bodies"]

    assert bodies[0]["path"] == "Lib/Huffman.cs"
    assert sum(len(row["source"]) for row in bodies) <= _BUDGET + 200
    assert len(bodies[0]["source"].splitlines()) > 1
    for row in bodies:
        start, end = row["lines"]
        assert end - start + 1 == len(row["source"].splitlines())
        assert row["truncated"] is True
        assert row["continuation"] == f"{row['path']}:{end + 1}-200"
        assert row["source_lines"] == 120
        assert row["source_lines_served"] == end - start + 1
        assert row["source_lines_cut_reason"] == "keyless_body_budget"
        assert not any(key.endswith(("_total", "_emitted")) for key in row)
    # The rest shrink to their signature line once the budget is spent.
    assert [len(row["source"].splitlines()) for row in bodies[1:]] == [1]
    assert out["_meta"]["projection"]["recovery"]["arguments"] == {"include": ["evidence"]}


@pytest.mark.parametrize("index", range(len(_SHAPES)))
def test_a_definition_in_the_cut_range_is_named(tmp_path: Path, index: int) -> None:
    out = project_answer_payload(
        _raw(tmp_path, top=index), question="which file decodes", repo_root=tmp_path
    )
    top = out["symbol_bodies"][0]
    inner = _SHAPES[index][4]

    assert top["path"] == _SHAPES[index][0]
    assert inner not in top["source"]
    assert any(row["line"] == 100 for row in top.get("withheld_symbols") or [])


def test_without_a_root_the_cut_still_points_at_the_rest(tmp_path: Path) -> None:
    raw = _raw(tmp_path)
    raw["symbol_bodies"][1]["withheld_symbols"] = [{"name": "tail", "line": 150}]

    top = project_answer_payload(raw, question="which file")["symbol_bodies"][0]

    assert top["path"] == _SHAPES[1][0]
    assert top["continuation"] == f"src/router.ts:{top['lines'][1] + 1}-200"
    assert top["withheld_symbols"] == [{"name": "tail", "line": 150}]


def test_a_small_keyless_payload_is_byte_identical(tmp_path: Path, monkeypatch) -> None:
    raw = _raw(tmp_path)
    for row in raw["symbol_bodies"]:
        row["source"] = "\n".join(row["source"].splitlines()[:3])
        row["lines"] = [1, 3]

    served = project_answer_payload(copy.deepcopy(raw), question="q", repo_root=tmp_path)
    monkeypatch.setattr(projection, "_budget_keyless_bodies", lambda payload, root: False)
    unbudgeted = project_answer_payload(copy.deepcopy(raw), question="q", repo_root=tmp_path)

    assert json.dumps(served, sort_keys=True) == json.dumps(unbudgeted, sort_keys=True)


@pytest.mark.parametrize(
    ("degraded", "include"),
    [(None, None), ("synthesis-failed", None), ("no-llm-provider", ["evidence"])],
)
def test_keyed_and_expanded_bodies_are_untouched(
    tmp_path: Path, monkeypatch, degraded: str | None, include: list[str] | None
) -> None:
    raw = _raw(tmp_path, degraded=degraded)

    served = project_answer_payload(
        copy.deepcopy(raw), question="q", include=include, repo_root=tmp_path
    )
    monkeypatch.setattr(projection, "_budget_keyless_bodies", lambda payload, root: False)
    unbudgeted = project_answer_payload(
        copy.deepcopy(raw), question="q", include=include, repo_root=tmp_path
    )

    assert json.dumps(served, sort_keys=True) == json.dumps(unbudgeted, sort_keys=True)
    assert all("source_lines" not in row for row in served.get("symbol_bodies") or [])


def test_a_form_feed_inside_a_body_does_not_shift_its_lines(tmp_path: Path) -> None:
    raw = _raw(tmp_path)
    top = raw["symbol_bodies"][1]
    top["source"] = "\n".join(f"x{i}\fy = {i}  # padding padding padding" for i in range(120))

    cut = project_answer_payload(raw, question="q", repo_root=tmp_path)["symbol_bodies"][0]

    assert cut["path"] == top["path"]
    served = cut["source"].split("\n")
    assert cut["lines"] == [1, len(served)]
    assert served[-1].startswith(f"x{len(served) - 1}\f")
    assert cut["continuation"].endswith(f":{len(served) + 1}-200")


def test_the_note_and_hint_point_at_the_new_continuation(tmp_path: Path) -> None:
    raw = _raw(tmp_path)
    raw["note"] = (
        "Synthesis is unavailable. symbol_bodies carries the live body of the symbol(s) "
        "you named, so answer from that rather than re-reading the file."
    )
    raw["next_action_hint"] = (
        "Router was served through line 120; call get_symbol id='src/router.ts:121-200' "
        "for the rest of it."
    )

    out = project_answer_payload(raw, question="q", repo_root=tmp_path)
    top = out["symbol_bodies"][0]

    assert "continuation" in out["note"] and "rather than re-reading" not in out["note"]
    assert f"served through line {top['lines'][1]};" in out["next_action_hint"]
    assert f"id='{top['continuation']}'" in out["next_action_hint"]
