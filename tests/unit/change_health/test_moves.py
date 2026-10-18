"""The move map: which symbols a change carried to another file."""

from __future__ import annotations

from repowise.core.analysis.change_health.analyzer import _file_info, language_of
from repowise.core.analysis.change_health.moves import (
    SymbolBody,
    build_move_map,
    symbol_bodies,
)
from repowise.core.ingestion.parser import parse_file

from .conftest import python_complex


def bodies(files: dict[str, str]) -> list[SymbolBody]:
    out: list[SymbolBody] = []
    for path, text in files.items():
        raw = text.encode("utf-8")
        parsed = parse_file(_file_info(path, language_of(path) or "", raw), raw)
        out.extend(symbol_bodies(path, raw, parsed.symbols))
    return out


def moves(base: dict[str, str], head: dict[str, str], rename_map=None):
    return build_move_map(bodies(base), bodies(head), rename_map=rename_map)


CONSTANTS = 'LIMIT = 30\n_NAMES = frozenset({"alpha", "beta"})\n\n\n'


def test_a_file_split_into_a_package_maps_every_symbol_to_its_origin():
    base = {"tool.py": CONSTANTS + python_complex("tangle", 6) + "\n\n" + python_complex("knot", 5)}
    head = {
        "tool/__init__.py": "from .a import tangle\nfrom .b import knot\n",
        "tool/a.py": python_complex("tangle", 6),
        "tool/b.py": CONSTANTS + python_complex("knot", 5),
    }

    result = moves(base, head)

    assert result.origin_of("tool/a.py", "tangle") == ("tool.py", "tangle")
    assert result.origin_of("tool/b.py", "knot") == ("tool.py", "knot")
    assert result.origin_of("tool/b.py", "LIMIT") == ("tool.py", "LIMIT")
    assert result.origin_of("tool/b.py", "_NAMES") == ("tool.py", "_NAMES")
    assert result.moved_total == 4
    assert not result.edited


def test_a_partial_split_maps_only_what_left_the_file():
    base = {"tool.py": python_complex("tangle", 6) + "\n\n" + python_complex("knot", 5)}
    head = {"tool.py": python_complex("tangle", 6), "knots.py": python_complex("knot", 5)}

    result = moves(base, head)

    assert dict(result.by_new) == {("knots.py", "knot"): ("tool.py", "knot")}


def test_a_reindent_inside_one_file_is_not_a_move():
    method = python_complex("tangle", 6, indent="    ")
    base = {"tool.py": python_complex("tangle", 6)}
    head = {"tool.py": "class Box:\n    # gathered here\n" + method}

    assert moves(base, head).moved_total == 0


def test_a_move_survives_reindenting_and_comment_edits():
    method = python_complex("tangle", 6, indent="    ").replace(
        "        value = value + 1\n", "        value = value + 1  # bump\n"
    )
    base = {"tool.py": "# old home\n" + python_complex("tangle", 6), "box.py": "X = 1\n"}
    head = {"tool.py": "# emptied\n", "box.py": "X = 1\n\n\nclass Box:\n" + method}

    assert moves(base, head).origin_of("box.py", "tangle") == ("tool.py", "tangle")


def test_a_renamed_file_is_not_a_move():
    body = python_complex("tangle", 6)
    result = moves({"old.py": body}, {"new.py": body}, rename_map={"old.py": "new.py"})

    assert result.moved_total == 0


def test_a_symbol_renamed_on_its_way_to_another_file_keeps_its_origin():
    base = {"tool.py": python_complex("tangle", 6)}
    head = {"tool.py": "", "knots.py": python_complex("knot", 6)}

    assert moves(base, head).origin_of("knots.py", "knot") == ("tool.py", "tangle")


def test_a_copy_beside_the_original_is_not_a_move():
    body = python_complex("tangle", 6)
    result = moves({"tool.py": body}, {"tool.py": body, "copy.py": body})

    assert result.moved_total == 0


def test_two_candidate_origins_for_one_body_pair_neither():
    body = python_complex("tangle", 6)
    result = moves({"a.py": body, "b.py": body}, {"a.py": "", "b.py": "", "c.py": body})

    assert result.moved_total == 0


def test_a_moved_and_edited_symbol_pairs_by_its_unique_name():
    base = {"tool.py": python_complex("tangle", 9)}
    head = {"tool.py": "", "knots.py": python_complex("tangle", 6)}

    result = moves(base, head)

    assert result.origin_of("knots.py", "tangle") == ("tool.py", "tangle")
    assert ("knots.py", "tangle") in result.edited


def test_a_short_body_never_pairs_under_a_new_name():
    base = {"a.py": "LIMIT = 30\n"}
    head = {"a.py": "", "b.py": "CAP = 30\n"}

    assert moves(base, head).moved_total == 0


def test_an_unrelated_function_of_the_same_name_is_not_an_edited_move():
    base = {"tool.py": python_complex("run", 12)}
    head = {"tool.py": "", "cli.py": "def run(argv):\n    return main(argv)\n"}

    assert moves(base, head).moved_total == 0


def test_a_key_shared_with_a_new_symbol_names_no_origin():
    """Findings look a symbol up by name, so a moved ``run`` beside a new one is unsafe."""
    moved = "class Old:\n" + python_complex("run", 6, indent="    ")
    fresh = "class New:\n" + python_complex("run", 3, indent="    ")
    result = moves({"a.py": moved}, {"a.py": "", "b.py": moved + fresh})

    assert result.origin_of("b.py", "run") is None
    assert result.origin_of("b.py", "Old") == ("a.py", "Old")


def test_a_copy_beside_an_original_edited_in_place_is_not_a_move():
    base = {"tool.py": python_complex("tangle", 6)}
    head = {"tool.py": python_complex("tangle", 7), "copy.py": python_complex("tangle", 6)}

    assert moves(base, head).moved_total == 0


def test_a_move_out_of_a_renamed_file_names_the_base_path():
    base = {"old.py": python_complex("tangle", 6) + "\n\n" + python_complex("knot", 5)}
    head = {"new.py": python_complex("tangle", 6), "knots.py": python_complex("knot", 5)}

    result = moves(base, head, rename_map={"old.py": "new.py"})

    assert dict(result.by_new) == {("knots.py", "knot"): ("old.py", "knot")}


def test_a_same_named_function_with_a_different_body_is_not_an_edited_move():
    """Similar size, unrelated code: the name alone is no evidence of a move."""
    other = "\n".join(
        ["def tangle(rows):", "    out = []"]
        + [f"    out.append(str(rows[{i}]).strip().lower())" for i in range(12)]
        + ["    return out", ""]
    )
    base = {"tool.py": python_complex("tangle", 6)}
    head = {"tool.py": "", "knots.py": other}

    assert moves(base, head).moved_total == 0
