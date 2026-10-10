"""A mention names one particular file only when its written path can mean that file."""

from __future__ import annotations

import pytest

from repowise.core.analysis.namer_paths import names_file

_UI = "packages/ui/package.json"


@pytest.mark.parametrize(
    ("text", "names"),
    [
        # A written path names the files ending in it, and those it ends in.
        ('ROOT / "packages/ui/package.json"', True),
        ('base / "ui/package.json"', True),
        (r'"packages\\ui\\package.json"', True),
        ('"/app/packages/ui/package.json"', True),
        (r'"C:\\repo\\packages\\ui\\package.json"', True),
        (r"C:\repo\packages\ui\package.json", True),
        ('"myrepo/packages/ui/package.json"', True),
        ('"file:///abs/repo/packages/ui/package.json"', True),
        ('"PACKAGES/UI/package.json"', True),
        ('ROOT / "packages/web/package.json"', False),
        ('"tests/fixtures/package.json"', False),
        ('"/app/packages/web/package.json"', False),
        # Relative steps are dropped: what is left decides.
        ("import p from '../ui/package.json'", True),
        ("import p from '../web/package.json'", False),
        ('"packages/web/../ui/package.json"', True),
        ("import p from '../package.json'", True),
        ("readFileSync('./package.json')", True),
        # Bare, joined to a computed base, a glob or a template: any such file.
        ('MANIFESTS = ("package.json",)', True),
        ('root + "/package.json"', True),
        ('f"{d}/package.json"', True),
        ('glob("packages/*/package.json")', True),
        ("the package.json.", True),
        ('"\\npackage.json"', True),
        ('"\\tpackage.json"', True),
        # Another file's name that only contains this one.
        ('"mypackage.json"', False),
        ('"package.json5"', False),
        ('"package.json.bak"', False),
        # One naming mention among others is enough.
        ('"packages/web/package.json", "packages/ui/package.json"', True),
    ],
)
def test_a_mention_names_the_file_its_written_path_can_mean(text, names) -> None:
    assert names_file(text, _UI) is names


def test_the_root_manifest_is_named_by_any_path_ending_in_its_name() -> None:
    # A root checkout can sit anywhere, so every written path to a file of
    # that name may be the root one.
    assert names_file('"package.json"', "package.json")
    assert names_file('"/abs/repo/package.json"', "package.json")
    assert names_file('"packages/ui/package.json"', "package.json")
    assert not names_file('"mypackage.json"', "package.json")
