"""PHP files a tool loads with no importer are live.

Composer's autoloader loads ``autoload.files``; PHPStan alone runs a
type-test corpus; lint tools read their config by name.
"""

from __future__ import annotations

import json
from pathlib import Path

from repowise.core.analysis.dead_code import DeadCodeAnalyzer
from repowise.core.ingestion import ASTParser, FileTraverser, GraphBuilder
from repowise.core.ingestion.phpstan import analysed_paths

_HELPERS = {
    "src/helpers.php": "<?php\nfunction money(int $cents): string { return ''; }\n",
    "src/legacy.php": "<?php\nfunction old_money(int $cents): string { return ''; }\n",
}

_TYPE_TEST = (
    "<?php\nuse function PHPStan\\Testing\\assertType;\n"
    "class Fixture {}\nassertType('int', 1);\n"
)


def _flagged(repo: Path, autoload: dict, files: dict[str, str]) -> set[tuple[str, str]]:
    (repo / "composer.json").write_text(json.dumps({"autoload": autoload}), encoding="utf-8")
    for rel, text in files.items():
        (repo / rel).parent.mkdir(parents=True, exist_ok=True)
        (repo / rel).write_text(text, encoding="utf-8")
    builder = GraphBuilder(repo)
    parser = ASTParser()
    for fi in FileTraverser(repo).traverse():
        builder.add_file(parser.parse_file(fi, Path(fi.abs_path).read_bytes()))
    builder.build()
    report = DeadCodeAnalyzer(builder.graph(), {}, repo_root=repo).analyze(
        {"detect_unused_internals": False}
    )
    return {(f.kind.value, f.file_path) for f in report.findings}


def _files(flagged: set[tuple[str, str]]) -> set[str]:
    return {path for _kind, path in flagged}


def test_an_autoloaded_helper_file_is_live_and_an_unlisted_one_is_not(tmp_path: Path) -> None:
    flagged = _flagged(tmp_path, {"files": ["src/helpers.php"]}, _HELPERS)

    assert "src/helpers.php" not in _files(flagged)
    assert ("unreachable_file", "src/legacy.php") in flagged


def test_without_the_listing_the_helper_file_is_flagged(tmp_path: Path) -> None:
    flagged = _flagged(tmp_path, {}, _HELPERS)

    assert ("unreachable_file", "src/helpers.php") in flagged


def test_a_phpstan_type_corpus_is_live(tmp_path: Path) -> None:
    flagged = _flagged(
        tmp_path,
        {"psr-4": {"Acme\\": "src/"}},
        {
            "phpstan.types.neon.dist": "parameters:\n    level: max\n    paths:\n        - types\n",
            "types/Cache.php": _TYPE_TEST,
            "types/Support.php": "<?php\nclass Support {}\n",
            # Analysed by PHPStan too, but no assertType: not a corpus.
            "phpstan.neon": "parameters:\n    paths:\n        - bin\n        - src\n",
            "bin/orphan.php": "<?php\nclass Orphan {}\n",
            "src/Unused.php": "<?php\nnamespace Acme;\nclass Unused {}\n",
        },
    )

    assert {"types/Cache.php", "types/Support.php"}.isdisjoint(_files(flagged))
    assert ("unreachable_file", "bin/orphan.php") in flagged
    assert ("unreachable_file", "src/Unused.php") in flagged


def test_lint_tool_configs_are_live(tmp_path: Path) -> None:
    flagged = _flagged(
        tmp_path,
        {},
        {
            "rector.php": "<?php\nreturn static function () {};\n",
            ".php-cs-fixer.dist.php": "<?php\nreturn [];\n",
        },
    )

    assert {"rector.php", ".php-cs-fixer.dist.php"}.isdisjoint(_files(flagged))


def test_analysed_paths_reads_only_the_paths_list() -> None:
    neon = (
        "includes:\n    - baseline.neon\nparameters:\n    paths:\n        - src\n"
        "        # a comment\n        - 'types/'\n        - %rootDir%/x\n"
        "    excludePaths:\n        - src/views\n"
    )
    assert analysed_paths(neon) == ["src", "types/"]
