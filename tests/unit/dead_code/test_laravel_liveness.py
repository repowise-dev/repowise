"""A Laravel app's convention-loaded files are not dead code.

Built through the real traverse -> parse -> graph -> framework-edges path, so
the test covers the whole chain: the composer reader, the framework facts, the
Laravel anchor edges and the same-namespace base-class edge.
"""

from __future__ import annotations

import json
from pathlib import Path

from repowise.core.analysis.dead_code import DeadCodeAnalyzer
from repowise.core.generation.editor_files.tech_stack import detect_tech_stack
from repowise.core.ingestion import ASTParser, FileTraverser, GraphBuilder

_FILES = {
    "routes/api.php": (
        "<?php\nuse App\\Http\\Controllers\\TicketController;\n"
        "use Illuminate\\Support\\Facades\\Route;\n"
        "Route::get('/tickets', [TicketController::class, 'index']);\n"
    ),
    "app/Http/Controllers/Controller.php": (
        "<?php\nnamespace App\\Http\\Controllers;\nabstract class Controller {}\n"
    ),
    "app/Http/Controllers/TicketController.php": (
        "<?php\nnamespace App\\Http\\Controllers;\nuse App\\Models\\Ticket;\n"
        "class TicketController extends Controller {\n"
        "    public function index() { return Ticket::all(); }\n}\n"
    ),
    "app/Models/Ticket.php": "<?php\nnamespace App\\Models;\nclass Ticket {}\n",
    "app/Providers/AppServiceProvider.php": (
        "<?php\nnamespace App\\Providers;\nclass AppServiceProvider {}\n"
    ),
    "app/Console/Commands/ReleaseHolds.php": (
        "<?php\nnamespace App\\Console\\Commands;\nclass ReleaseHolds {}\n"
    ),
    "app/Jobs/NeverDispatched.php": "<?php\nnamespace App\\Jobs;\nclass NeverDispatched {}\n",
    "database/factories/TicketFactory.php": (
        "<?php\nnamespace Database\\Factories;\nclass TicketFactory {}\n"
    ),
    "config/ticketing.php": "<?php\nreturn ['hold_minutes' => 10];\n",
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
                "autoload": {
                    "psr-4": {"App\\": "app/", "Database\\Factories\\": "database/factories/"}
                },
            }
        ),
        encoding="utf-8",
    )
    for rel, text in _FILES.items():
        path = repo / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")


_CONVENTION_FILES = (
    "routes/api.php",
    "app/Providers/AppServiceProvider.php",
    "app/Console/Commands/ReleaseHolds.php",
    "database/factories/TicketFactory.php",
    "config/ticketing.php",
)


def test_convention_files_are_live_and_an_undispatched_job_is_not(tmp_path: Path) -> None:
    _write_app(tmp_path, {"laravel/framework": "^11.0"})

    flagged = {path for _kind, path in _analyze(tmp_path)}

    for live in (*_CONVENTION_FILES, "app/Http/Controllers/Controller.php"):
        assert live not in flagged, live
    # Code has to dispatch a job, so one nothing names really is unused.
    assert "app/Jobs/NeverDispatched.php" in flagged


def test_without_the_framework_the_same_files_are_flagged(tmp_path: Path) -> None:
    # The control: nothing but the Laravel requirement keeps them alive.
    _write_app(tmp_path, {"php": "^8.2"})

    flagged = {path for _kind, path in _analyze(tmp_path)}

    for dead in _CONVENTION_FILES:
        assert dead in flagged, dead
    # The base class is reached through its subclasses either way.
    assert "app/Http/Controllers/Controller.php" not in flagged


_REGISTERED = {
    "app/Events/TicketSold.php": "<?php\nnamespace App\\Events;\nclass TicketSold {}\n",
    "app/Http/Controllers/SaleController.php": (
        "<?php\nnamespace App\\Http\\Controllers;\nuse App\\Events\\TicketSold;\n"
        "class SaleController extends Controller {\n"
        "    public function store() { event(new TicketSold()); }\n}\n"
    ),
    "app/Listeners/SendReceipt.php": (
        "<?php\nnamespace App\\Listeners;\nuse App\\Events\\TicketSold;\n"
        "class SendReceipt { public function handle(TicketSold $event): void {} }\n"
    ),
    "app/Listeners/Orphan.php": (
        "<?php\nnamespace App\\Listeners;\nclass Orphan { public function handle($event): void {} }\n"
    ),
    "app/Policies/TicketPolicy.php": "<?php\nnamespace App\\Policies;\nclass TicketPolicy {}\n",
    "app/Policies/NoModelPolicy.php": "<?php\nnamespace App\\Policies;\nclass NoModelPolicy {}\n",
}


def test_discovered_listeners_and_policies_live_and_orphans_do_not(tmp_path: Path) -> None:
    # Listeners and policies are linked from what registers or discovers them,
    # not anchored by directory, so one nothing reaches still reads as unused.
    _write_app(tmp_path, {"laravel/framework": "^11.0"})
    for rel, text in _REGISTERED.items():
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    flagged = {path for _kind, path in _analyze(tmp_path)}

    assert "app/Listeners/SendReceipt.php" not in flagged
    assert "app/Policies/TicketPolicy.php" not in flagged
    assert "app/Listeners/Orphan.php" in flagged
    assert "app/Policies/NoModelPolicy.php" in flagged
