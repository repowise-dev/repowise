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
        TOOL.replace("    total += 2\n", "    total += 20\n").replace(
            "def delete_me(value):\n    return value * 2\n\n\n", ""
        )
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


# -- the (path, name) set that ties a finding to an edited symbol -----------


def _sym(name, start, end, parent=None):
    from repowise.core.ingestion.models import Symbol

    local = f"{parent}::{name}" if parent else name
    return Symbol(
        id=f"tool.py::{local}",
        name=name,
        qualified_name=local.replace("::", "."),
        kind="method" if parent else "function",
        signature=f"def {name}()",
        start_line=start,
        end_line=end,
        docstring=None,
        parent_name=parent,
    )


def _change(new_lines=(), old_ranges=(), status="modified"):
    from repowise.core.analysis.change_health.sources import FileChange
    from repowise.core.analysis.changed_lines import FileDiff

    diff = FileDiff("tool.py", new_lines=set(new_lines), old_ranges=list(old_ranges))
    return FileChange("tool.py", "tool.py", status, diff)


def _finding(name, start, end=None):
    from repowise.core.analysis.health.models import HealthFindingData

    return HealthFindingData(
        biomarker_type="io_in_loop",
        severity="medium",
        file_path="tool.py",
        function_name=name,
        line_start=start,
        line_end=end or start,
        details={},
        health_impact=1.0,
    )


HEAD = [_sym("first", 1, 5), _sym("second", 7, 12), _sym("third", 14, 20)]


def _pairs(change, findings=(), head=HEAD, base=HEAD):
    from repowise.core.analysis.change_health.attribution import changed_symbols_for

    return changed_symbols_for(
        {"tool.py": change}, {"tool.py": list(findings)}, {"tool.py": head}, {"tool.py": base}
    )


def test_an_edit_away_from_every_finding_still_names_its_symbol():
    # The only finding sits on line 9; the edit is on line 11.
    got = _pairs(_change(new_lines={11}), [_finding("second", 9)])

    assert got == {("tool.py", "second")}


def test_a_hunk_spanning_two_symbols_names_both():
    got = _pairs(_change(new_lines={4, 5, 6, 7, 8}))

    assert got == {("tool.py", "first"), ("tool.py", "second")}


def test_lines_deleted_inside_a_body_name_that_symbol():
    got = _pairs(_change(old_ranges=[(16, 17)]))

    assert got == {("tool.py", "third")}


def test_a_finding_span_still_names_a_symbol_the_ranges_lack():
    # ``inner`` is no symbol of its own (a nested closure the parser skipped).
    got = _pairs(_change(new_lines={3}), [_finding("inner", 2, 4)])

    assert got == {("tool.py", "first"), ("tool.py", "inner")}


def test_a_name_declared_twice_is_claimed_only_when_both_are_touched():
    head = [_sym("A", 1, 6), _sym("run", 2, 6, "A"), _sym("B", 8, 13), _sym("run", 9, 13, "B")]

    one = _pairs(_change(new_lines={4}), head=head, base=head)
    both = _pairs(_change(new_lines={4, 11}), head=head, base=head)

    assert one == {("tool.py", "A")}
    assert both == {("tool.py", "A"), ("tool.py", "B"), ("tool.py", "run")}


def test_the_attributor_charges_an_edited_symbol_not_the_file():
    from repowise.core.analysis.change_health.attribution import FindingAttributor

    change = _change(new_lines={11})
    finding = _finding("second", 9)
    pairs = _pairs(change, [finding])

    got = FindingAttributor({"tool.py": change}).attribute(finding, changed_symbols=pairs)

    assert got.basis == "changed_symbol"
    # A finding in a symbol the change never reached stays a file-level edit.
    other = _finding("third", 15)
    assert (
        FindingAttributor({"tool.py": change}).attribute(other, changed_symbols=pairs).basis
        == "file_change"
    )
