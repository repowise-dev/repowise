"""A mention names one particular file only when its written directory can lead there."""

from __future__ import annotations

import pytest

from repowise.core.analysis.namer_paths import names_file

_UI = "packages/ui/package.json"


@pytest.mark.parametrize(
    ("namer", "text", "names"),
    [
        # Written with its directory: that file, or any path ending in it.
        ("tests/unit/t.py", 'ROOT / "packages/ui/package.json"', True),
        ("tests/unit/t.py", 'base / "ui/package.json"', True),
        ("tests/unit/t.py", r'"packages\\ui\\package.json"', True),
        ("tests/unit/t.py", 'ROOT / "packages/web/package.json"', False),
        ("tests/unit/t.py", '"tests/fixtures/package.json"', False),
        # Relative: from the namer's directory or one above it.
        ("packages/ui/src/a.ts", "import p from '../package.json'", True),
        ("packages/ui/src/deep/a.ts", "readFileSync('./package.json')", True),
        ("packages/web/src/a.ts", "import p from '../package.json'", False),
        ("packages/core/x.py", '"./package.json"', False),
        ("a.py", '"../package.json"', True),  # leaves the repository: cannot tell
        # Bare, joined to a computed base, a glob or a template: any such file.
        ("packages/core/x.py", 'MANIFESTS = ("package.json",)', True),
        ("packages/core/x.py", 'root + "/package.json"', True),
        ("tests/unit/t.py", 'f"{d}/package.json"', True),
        ("tests/unit/t.py", 'glob("packages/*/package.json")', True),
        ("tests/unit/t.py", "the package.json.", True),
        # Another file's name that only contains this one.
        ("tests/unit/t.py", '"mypackage.json"', False),
        ("tests/unit/t.py", '"package.json5"', False),
        ("tests/unit/t.py", '"package.json.bak"', False),
        # One naming mention among others is enough.
        ("tests/unit/t.py", '"packages/web/package.json", "packages/ui/package.json"', True),
    ],
)
def test_a_mention_names_the_file_its_directory_can_lead_to(namer, text, names) -> None:
    assert names_file(text, namer, _UI) is names


def test_the_root_manifest_is_named_by_a_bare_or_root_relative_mention() -> None:
    assert names_file('"package.json"', "scripts/x.py", "package.json")
    assert names_file("require('../package.json')", "scripts/x.js", "package.json")
    assert not names_file('"packages/ui/package.json"', "scripts/x.py", "package.json")
