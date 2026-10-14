"""get_answer retrieval: rare symbol-leg terms, the literal leg, and the topic guard.

A question's identifier anchors a definition only when the topic agrees; an
identifier the index does not define (a string literal, a key) anchors the
function holding it, so ``symbol_bodies`` serves the body the wiki never
rendered.
"""

from __future__ import annotations

import pytest

from repowise.server.mcp_server._hit_symbols import _stem
from repowise.server.mcp_server._prose_symbols import _rarest_terms, _SymbolTextIndex
from repowise.server.mcp_server.tool_answer.bodies import _gather_body_candidates
from repowise.server.mcp_server.tool_answer.config import _INLINE_BODY_MAX_LINES
from repowise.server.mcp_server.tool_answer.symbols import (
    _anchor_symbol_hits,
    drop_ruled_out_files,
)


class _Sym:
    def __init__(self, name, file_path, start_line, end_line, kind="function", parent_name=None):
        self.name = name
        self.file_path = file_path
        self.kind = kind
        self.parent_name = parent_name
        self.qualified_name = name
        self.start_line = start_line
        self.end_line = end_line
        self.symbol_id = f"{file_path}::{name}"


class _Result:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return self

    def all(self):
        return self._rows


class _Session:
    """Answers each query with the next row list: by name, then by file."""

    def __init__(self, *answers):
        self._answers = list(answers)

    async def execute(self, *a, **k):
        return _Result(self._answers.pop(0) if self._answers else [])


_SPEC = "class Spec:\n    def module_path(self):\n        return 1\n"


def _write(root, rel, text):
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _index(counts: dict[str, int]) -> _SymbolTextIndex:
    """Name postings carrying each word *counts* times, keyed as the index keys them."""
    return _SymbolTextIndex((), (), (), {_stem(w): tuple(range(n)) for w, n in counts.items()})


def test_rarest_terms_keep_the_rare_word_over_the_long_one():
    # ``resolution`` is longer but six names carry it; ``alias`` only one.
    index = _index({"resolution": 6, "alias": 1, "call": 2})
    kept = _rarest_terms(index, ["resolution", "alias", "call", "unheard"], 2)
    assert kept == ["alias", "call"]


def test_rarest_terms_rank_a_compound_by_its_rarest_word():
    index = _index({"module": 9, "alias": 1})
    assert _rarest_terms(index, ["module", "module_alias"], 1) == ["module_alias"]


@pytest.mark.asyncio
async def test_a_name_written_across_the_repo_does_not_anchor_an_off_topic_def(tmp_path):
    _write(tmp_path, "providers/specs.py", _SPEC)
    for i in range(4):
        _write(tmp_path, f"core/user{i}.py", f"module_path = {i}\n")
    row = _Sym("module_path", "providers/specs.py", 2, 3, kind="method", parent_name="Spec")
    hits = [{"target_path": "core/imports.py", "score": 3.0}]
    out, homonyms = await _anchor_symbol_hits(
        _Session([row]),
        "r",
        {"module_path"},
        hits,
        repo_root=tmp_path,
        question="how are wildcard import bindings consumed (module_path)?",
    )
    assert out[0]["target_path"] == "core/imports.py"
    assert not any(h.get("_anchor_symbols") for h in out)
    assert not homonyms["union"]


@pytest.mark.asyncio
async def test_a_specific_name_still_anchors_a_def_retrieval_missed(tmp_path):
    _write(tmp_path, "providers/specs.py", _SPEC)
    row = _Sym("module_path", "providers/specs.py", 2, 3, kind="method", parent_name="Spec")
    hits = [{"target_path": "core/imports.py", "score": 3.0}]
    out, _ = await _anchor_symbol_hits(
        _Session([row]),
        "r",
        {"module_path"},
        hits,
        repo_root=tmp_path,
        question="how are wildcard import bindings consumed (module_path)?",
    )
    assert out[0]["target_path"] == "providers/specs.py"
    assert out[0]["_anchor_symbols"][0]["name"] == "module_path"


@pytest.mark.asyncio
async def test_test_fixtures_sharing_a_production_name_are_not_the_answer(tmp_path):
    for i in range(4):
        _write(tmp_path, f"pipeline/stage{i}.py", f"parsed_files = [{i}]\n")
    for i in range(2):
        _write(tmp_path, f"tests/test_walk{i}.py", "def parsed_files():\n    return []\n")
    rows = [_Sym("parsed_files", f"tests/test_walk{i}.py", 1, 2) for i in range(2)]
    hits = [{"target_path": "pipeline/stage0.py", "score": 3.0}]
    out, homonyms = await _anchor_symbol_hits(
        _Session(rows),
        "r",
        {"parsed_files"},
        hits,
        repo_root=tmp_path,
        question="Is a manifest included in parsed_files during the ingestion walk?",
    )
    assert not homonyms["union"]
    assert not any(h.get("_anchor_symbols") for h in out)


@pytest.mark.asyncio
async def test_a_bare_lookup_keeps_trusting_the_name(tmp_path):
    for i in range(4):
        _write(tmp_path, f"pipeline/stage{i}.py", f"parsed_files = [{i}]\n")
    _write(tmp_path, "tests/test_walk.py", "def parsed_files():\n    return []\n")
    row = _Sym("parsed_files", "tests/test_walk.py", 1, 2)
    out, _ = await _anchor_symbol_hits(
        _Session([row]), "r", {"parsed_files"}, [], repo_root=tmp_path, question="parsed_files"
    )
    assert out[0]["target_path"] == "tests/test_walk.py"


@pytest.mark.asyncio
async def test_a_literal_no_symbol_names_anchors_the_function_holding_it(tmp_path):
    source = (
        "def _pick_origin(call):\n"
        "    if call.alias:\n"
        '        return "module_alias"\n'
        "    return None\n"
    )
    _write(tmp_path, "core/resolver.py", source)
    holder = _Sym("_pick_origin", "core/resolver.py", 1, 4)
    hits = [{"target_path": "core/other.py", "score": 3.0}]
    out, _ = await _anchor_symbol_hits(
        _Session([], [holder]),
        "r",
        {"module_alias"},
        hits,
        repo_root=tmp_path,
        question="where is the module_alias resolution origin set?",
    )
    assert out[0]["target_path"] == "core/resolver.py"
    [anchor] = out[0]["_anchor_symbols"]
    assert anchor["name"] == "_pick_origin" and anchor["_named_by"] == "module_alias"
    # The keyless reply selects bodies by the question's identifiers.
    [picked] = _gather_body_candidates(out, "", anchor_names={"module_alias"})
    assert picked[4]["name"] == "_pick_origin"


@pytest.mark.asyncio
async def test_a_prose_word_is_not_scanned_for(tmp_path):
    _write(tmp_path, "core/auth.py", 'def strip(h):\n    h.pop("Authorization")\n')
    holder = _Sym("strip", "core/auth.py", 1, 2)
    out, _ = await _anchor_symbol_hits(
        _Session([], [holder]),
        "r",
        {"Authorization"},
        [{"target_path": "core/other.py", "score": 3.0}],
        repo_root=tmp_path,
        question="where is the Authorization header removed on redirect?",
    )
    assert not any(h.get("_anchor_symbols") for h in out)



def _class_file(name: str, length: int = 200) -> str:
    lines = [f"public class {name} {{", "  int x;", "", "", f"  {name}(int x) {{", "  }"]
    return "\n".join(lines + ["  // body"] * (length - len(lines) - 1) + ["}"]) + "\n"


def _class_and_constructors(name: str, path: str, ctors: int, end: int = 200) -> list[_Sym]:
    rows = [_Sym(name, path, 1, end, kind="class")]
    rows += [_Sym(name, path, 5, 6, kind="method", parent_name=name) for _ in range(ctors)]
    return rows


@pytest.mark.asyncio
async def test_a_class_and_its_constructors_are_one_definition(tmp_path):
    path = "stream/JsonReader.java"
    _write(tmp_path, path, _class_file("JsonReader"))
    rows = _class_and_constructors("JsonReader", path, 2)
    hits = [{"target_path": path, "score": 3.0}]
    out, homonyms = await _anchor_symbol_hits(
        _Session(list(rows)), "r", {"JsonReader"}, hits, repo_root=tmp_path,
        question="How does JsonReader skip the non-execute prefix?",
    )
    assert not homonyms["union"]
    assert out[0]["target_path"] == path and out[0]["_symbol_anchored"]
    # Prose: the class head a cap would cut answers nothing; its file still leads.
    assert not out[0].get("_anchor_symbols")
    # A bare lookup still serves the class.
    out, _ = await _anchor_symbol_hits(
        _Session(list(rows)), "r", {"JsonReader"}, [], repo_root=tmp_path, question="JsonReader"
    )
    assert [(a["name"], a["kind"]) for a in out[0]["_anchor_symbols"]] == [("JsonReader", "class")]


@pytest.mark.asyncio
@pytest.mark.parametrize("extra, stashed", [(0, True), (1, False)])
async def test_a_class_head_is_skipped_exactly_when_the_inline_cap_cuts_it(tmp_path, extra, stashed):
    end = 1 + _INLINE_BODY_MAX_LINES + extra
    path = "stream/JsonReader.java"
    _write(tmp_path, path, _class_file("JsonReader", end))
    out, _ = await _anchor_symbol_hits(
        _Session(_class_and_constructors("JsonReader", path, 1, end)), "r", {"JsonReader"},
        [{"target_path": path, "score": 3.0}], repo_root=tmp_path,
        question="How does JsonReader skip the non-execute prefix?",
    )
    assert bool(out[0].get("_anchor_symbols")) is stashed


@pytest.mark.asyncio
async def test_a_plain_word_project_name_does_not_anchor_its_class(tmp_path):
    # ``Gson`` names the project, its main class and its constructors.
    path = "src/main/java/com/google/gson/Gson.java"
    _write(tmp_path, path, _class_file("Gson"))
    for i in range(4):
        _write(tmp_path, f"src/main/java/com/google/gson/Use{i}.java", "Gson g = new Gson();\n")
    hits = [{"target_path": path, "score": 3.0}]
    out, homonyms = await _anchor_symbol_hits(
        _Session(_class_and_constructors("Gson", path, 2)), "r", {"Gson", "Java"}, hits,
        repo_root=tmp_path,
        question="Where does Gson throw the duplicate key error on a Java map?",
    )
    assert not homonyms["union"]
    assert not any(h.get("_symbol_anchored") for h in out)


def test_a_file_the_question_rules_out_leaves_the_hits():
    hits = [
        {"target_path": "core/symbol_identity.py"},
        {"target_path": "core/symbol_identity.py::split_id"},
        {"target_path": "core/call_resolver.py"},
        {"target_path": "legacy/old.py"},
    ]
    kept = drop_ruled_out_files(hits, "Which code parses the suffix, outside symbol_identity.py?")
    assert [h["target_path"] for h in kept] == ["core/call_resolver.py", "legacy/old.py"]
    kept = drop_ruled_out_files(hits, "Who reads the suffix other than legacy/?")
    assert [h["target_path"] for h in kept][-1] == "core/call_resolver.py"
    # A path with a directory rules out only that file; a bare name, every file of it.
    twins = [{"target_path": "a/utils.py"}, {"target_path": "b/utils.py"}]
    assert drop_ruled_out_files(twins, "who escapes it outside b/utils.py?") == twins[:1]
    assert drop_ruled_out_files(twins, "who escapes it outside utils.py?") == []
    # Prose and a dotted module path rule nothing out.
    assert drop_ruled_out_files(hits, "what runs outside the loop, besides a.b.c?") == hits


@pytest.mark.asyncio
async def test_a_name_only_tests_write_does_not_anchor_a_fixture(tmp_path):
    # No production file writes the name, which must not read as "specific".
    _write(tmp_path, "core/ingest.py", "def walk():\n    return []\n")
    _write(tmp_path, "tests/test_walk.py", "def walked_paths():\n    return []\n")
    row = _Sym("walked_paths", "tests/test_walk.py", 1, 2)
    out, homonyms = await _anchor_symbol_hits(
        _Session([row]),
        "r",
        {"walked_paths"},
        [{"target_path": "core/ingest.py", "score": 3.0}],
        repo_root=tmp_path,
        question="does the ingestion walk include walked_paths for manifests?",
    )
    assert not homonyms["union"]
    assert not any(h.get("_anchor_symbols") for h in out)
