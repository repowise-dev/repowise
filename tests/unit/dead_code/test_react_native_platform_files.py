"""React Native platform files are reachable, not dead code (#3603).

Metro resolves ``import Button from './Button'`` to ``Button.ios.tsx`` /
``Button.android.tsx`` and ``./haptics`` to ``haptics.native.ts`` as well as
``haptics.ts``. Built through the real traverse -> parse -> graph path.
"""

from __future__ import annotations

import json
from pathlib import Path

from repowise.core.analysis.dead_code import DeadCodeAnalyzer
from repowise.core.ingestion import ASTParser, FileTraverser, GraphBuilder

_FILES = {
    "package.json": json.dumps({"dependencies": {"react-native": "0.76.0", "react": "18.3.1"}}),
    "index.js": (
        "import App from './src/App';\n"
        "AppRegistry.registerComponent('rnapp', () => App);\n"
    ),
    "src/App.tsx": (
        "import Button from './components/Button';\n"
        "import { haptic } from './components/haptics';\n"
        "export default function App() { haptic(); return Button(); }\n"
    ),
    "src/components/Button.ios.tsx": "export default function Button() { return null; }\n",
    "src/components/Button.android.tsx": "export default function Button() { return null; }\n",
    "src/components/haptics.ts": "export function haptic() {}\n",
    "src/components/haptics.native.ts": "export function haptic() {}\n",
}

_PLATFORM_FILES = (
    "src/components/Button.ios.tsx",
    "src/components/Button.android.tsx",
    "src/components/haptics.ts",
    "src/components/haptics.native.ts",
)


def _build(repo: Path) -> tuple[GraphBuilder, dict[str, bytes]]:
    builder = GraphBuilder(repo)
    parser = ASTParser()
    source_map: dict[str, bytes] = {}
    for fi in FileTraverser(repo).traverse():
        src = Path(fi.abs_path).read_bytes()
        builder.add_file(parser.parse_file(fi, src))
        source_map[fi.path] = src
    builder.set_source_map(source_map)
    builder.build()
    return builder, source_map


def _write(repo: Path, files: dict[str, str]) -> None:
    for rel, text in files.items():
        path = repo / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")


def _flagged(repo: Path) -> set[tuple[str, str]]:
    builder, source_map = _build(repo)
    report = DeadCodeAnalyzer(
        builder.graph(), {}, parsed_files=builder._parsed_files, source_map=source_map, repo_root=repo
    ).analyze({})
    return {(f.kind.value, f.file_path) for f in report.findings}


def test_app_imports_every_platform_file(tmp_path: Path) -> None:
    _write(tmp_path, _FILES)
    builder, _ = _build(tmp_path)

    edges = {(u, v) for u, v in builder.graph().edges if u == "src/App.tsx"}

    for target in _PLATFORM_FILES:
        assert ("src/App.tsx", target) in edges, target


def test_platform_files_are_not_reported_as_dead(tmp_path: Path) -> None:
    _write(tmp_path, _FILES)

    flagged_files = {path for _kind, path in _flagged(tmp_path)}

    for live in _PLATFORM_FILES:
        assert live not in flagged_files, live


def test_a_platform_file_nothing_imports_is_still_dead(tmp_path: Path) -> None:
    # The control: only an import keeps a platform file alive.
    _write(tmp_path, {**_FILES, "src/components/Orphan.ios.tsx": "export const Orphan = 1;\n"})

    flagged_files = {path for _kind, path in _flagged(tmp_path)}

    assert "src/components/Orphan.ios.tsx" in flagged_files
