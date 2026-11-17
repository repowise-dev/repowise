"""Every symbol a diff touches, not only the ones holding a finding."""

from __future__ import annotations

from repowise.core.analysis.change_health.service import ChangeHealthDeltaService, DeltaRequest

TOOL = """\
def keep(value):
    return value + 1


def edit_me(value):
    total = value
    total += 2
    total += 3
    return total


def delete_me(value):
    return value * 2


class Box:
    def first(self):
        return 1

    def second(self):
        a = 1
        b = 2
        return a + b
"""

MOVED = """\
def travel(value):
    out = []
    for item in range(value):
        if item % 2 == 0:
            out.append(item * 3)
        else:
            out.append(item - 1)
    return sorted(out, reverse=True)
"""


def compare(repo):
    service = ChangeHealthDeltaService(repo_path=str(repo.path))
    return service.compare(DeltaRequest(str(repo.path), "HEAD"))


def rows(delta):
    return {(r.path, r.symbol): r.change for r in delta.changed_symbols}


def test_added_edited_deleted_and_moved_symbols(make_repo):
    repo = make_repo()
    calm = "def calm():\n    return 0\n"
    repo.commit("base", {"tool.py": TOOL + "\n\n" + MOVED, "untouched.py": calm})
    head = (
        TOOL.replace("    total += 2\n", "    total += 20\n")
        .replace("def delete_me(value):\n    return value * 2\n\n\n", "")
        + "\n\ndef brand_new(value):\n    return -value\n"
    )
    repo.commit("head", {"tool.py": head, "pkg/travel.py": MOVED})

    delta = compare(repo)
    got = rows(delta)

    assert got == {
        ("tool.py", "edit_me"): "edited",
        ("tool.py", "brand_new"): "added",
        ("tool.py", "delete_me"): "deleted",
        ("pkg/travel.py", "travel"): "moved",
    }
    moved = next(r for r in delta.changed_symbols if r.change == "moved")
    assert moved.moved_from == ("tool.py", "travel")


def test_a_method_edit_names_the_method_not_its_class(make_repo):
    repo = make_repo()
    repo.commit("base", {"tool.py": TOOL})
    repo.commit("head", {"tool.py": TOOL.replace("b = 2", "b = 3")})

    changed = compare(repo).changed_symbols

    assert [(r.symbol, r.change) for r in changed] == [("Box::second", "edited")]
    assert (changed[0].start_line, changed[0].end_line) == (20, 23)


def test_lines_deleted_inside_a_body_edit_that_symbol(make_repo):
    repo = make_repo()
    repo.commit("base", {"tool.py": TOOL})
    repo.commit("head", {"tool.py": TOOL.replace("    total += 3\n", "")})

    assert rows(compare(repo)) == {("tool.py", "edit_me"): "edited"}


def test_a_new_file_adds_every_symbol_and_a_deleted_file_deletes_them(make_repo):
    repo = make_repo()
    repo.commit("base", {"old.py": "def gone():\n    return 1\n"})
    repo.remove("old.py")
    repo.commit("head", {"fresh.py": "class Fresh:\n    def make(self):\n        return 2\n"})

    assert rows(compare(repo)) == {
        ("fresh.py", "Fresh"): "added",
        ("fresh.py", "Fresh::make"): "added",
        ("old.py", "gone"): "deleted",
    }


def test_lines_deleted_at_the_end_of_a_body_edit_that_symbol(make_repo):
    repo = make_repo()
    repo.commit("base", {"tool.py": TOOL})
    repo.commit("head", {"tool.py": TOOL.replace("        return a + b\n", "")})

    assert rows(compare(repo)) == {("tool.py", "Box::second"): "edited"}


def test_a_method_added_to_a_class_does_not_charge_the_class(make_repo):
    repo = make_repo()
    repo.commit("base", {"tool.py": TOOL})
    extra = "\n    def third(self):\n        return 3\n"
    repo.commit("head", {"tool.py": TOOL + extra})

    assert rows(compare(repo)) == {("tool.py", "Box::third"): "added"}


def test_a_renamed_file_keeps_its_symbols_identity(make_repo):
    repo = make_repo()
    repo.commit("base", {"tool.py": TOOL})
    repo.move("tool.py", "renamed.py")
    repo.commit("head", {"renamed.py": TOOL.replace("total += 2", "total += 7")})

    assert rows(compare(repo)) == {("renamed.py", "edit_me"): "edited"}


def test_a_move_out_of_a_kept_file_is_a_move_not_a_deletion(make_repo):
    repo = make_repo()
    calm = "def calm():\n    return 0\n"
    repo.commit("base", {"tool.py": TOOL + "\n\n" + MOVED, "other.py": calm})
    repo.commit("head", {"tool.py": TOOL, "other.py": calm + "\n\n" + MOVED})

    assert rows(compare(repo)) == {("other.py", "travel"): "moved"}
