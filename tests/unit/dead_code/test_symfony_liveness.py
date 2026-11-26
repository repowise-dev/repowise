"""A Symfony app's commands and event subscribers/listeners are not dead code.

Built through the real traverse -> parse -> graph -> framework-edges path,
so the test covers the whole chain: the framework facts, the Symfony anchor
edges, attribute handling, and resolved base-class inheritance.
"""

from __future__ import annotations

import json
from pathlib import Path

from repowise.core.analysis.dead_code import DeadCodeAnalyzer
from repowise.core.generation.editor_files.tech_stack import detect_tech_stack
from repowise.core.ingestion import ASTParser, FileTraverser, GraphBuilder

_FILES = {
    "src/Cmd/Hello.php": (
        "<?php\nnamespace Acme\\Cmd;\n"
        "use Symfony\\Component\\Console\\Command\\Command;\n"
        "class Hello extends Command {\n"
        "    public function execute() { return 0; }\n}\n"
    ),
    "src/Cmd/ChildCommand.php": (
        "<?php\nnamespace Acme\\Cmd;\n"
        "class ChildCommand extends Hello {\n"
        "    public function execute() { return 0; }\n}\n"
    ),
    "src/Cmd/AttrCommand.php": (
        "<?php\nnamespace Acme\\Cmd;\n"
        "use Symfony\\Component\\Console\\Attribute\\AsCommand;\n"
        "#[AsCommand(name: 'app:greet')]\n"
        "class AttrCommand {\n"
        "    public function execute() { return 0; }\n}\n"
    ),
    "src/Events/Sub.php": (
        "<?php\nnamespace Acme\\Events;\n"
        "use Symfony\\Component\\EventDispatcher\\EventSubscriberInterface;\n"
        "class Sub implements EventSubscriberInterface {\n"
        "    public static function getSubscribedEvents() { return []; }\n}\n"
    ),
    "src/Events/AttrListener.php": (
        "<?php\nnamespace Acme\\Events;\n"
        "use Symfony\\Component\\EventDispatcher\\Attribute\\AsEventListener;\n"
        "#[AsEventListener]\n"
        "class AttrListener {\n"
        "    public function onKernelRequest() {}\n}\n"
    ),
    "src/Local/LocalBase.php": "<?php\nnamespace Acme\\Local;\nclass LocalBase {}\n",
    "src/Local/LocalCommand.php": (
        "<?php\nnamespace Acme\\Local;\n"
        "class LocalCommand extends LocalBase {}\n"
    ),
    "src/Util/Plain.php": "<?php\nnamespace Acme\\Util;\nclass Plain {}\n",
}


def _analyze(repo: Path) -> set[tuple[str, str]]:
    builder = GraphBuilder(repo)
    parser = ASTParser()
    source_map: dict[str, bytes] = {}
    for fi in FileTraverser(repo).traverse():
        src = Path(fi.abs_path).read_bytes()
        builder.add_file(parser.parse_file(fi, src))
        source_map[fi.path] = src
    builder.set_source_map(source_map)
    builder.build()
    builder.add_framework_edges([item.name for item in detect_tech_stack(repo)])
    report = DeadCodeAnalyzer(
        builder.graph(), {}, parsed_files=builder._parsed_files, source_map=source_map, repo_root=repo
    ).analyze({"detect_unused_internals": False})
    return {(f.kind.value, f.file_path) for f in report.findings}


def _write_app(repo: Path, require: dict[str, str]) -> None:
    (repo / "composer.json").write_text(
        json.dumps(
            {
                "require": require,
                "autoload": {"psr-4": {"Acme\\": "src/"}},
            }
        ),
        encoding="utf-8",
    )
    for rel, text in _FILES.items():
        path = repo / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")


def test_symfony_commands_and_subscribers_are_live(tmp_path: Path) -> None:
    _write_app(tmp_path, {"symfony/console": "^6.0", "symfony/event-dispatcher": "^6.0"})

    flagged = {path for _kind, path in _analyze(tmp_path)}

    live_files = (
        "src/Cmd/Hello.php",
        "src/Cmd/ChildCommand.php",
        "src/Cmd/AttrCommand.php",
        "src/Events/Sub.php",
        "src/Events/AttrListener.php",
    )
    for live in live_files:
        assert live not in flagged, f"{live} should be live but was flagged"

    # Plain and local non-Symfony command remain flagged as dead code
    assert "src/Util/Plain.php" in flagged
    assert "src/Local/LocalCommand.php" in flagged
