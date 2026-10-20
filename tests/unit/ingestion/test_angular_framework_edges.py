"""Angular build entries, router config and decorator metadata as graph edges.

The end-to-end cases go through traverse -> parse -> graph -> framework edges
-> dead code, the path a real index takes.
"""

from __future__ import annotations

import json
from pathlib import Path

from repowise.core.analysis.dead_code import DeadCodeAnalyzer
from repowise.core.ingestion import ASTParser, FileTraverser, GraphBuilder
from repowise.core.ingestion.framework_edges.angular import angular_references
from repowise.core.ingestion.framework_facts import angular_entry_files

_ANGULAR_JSON = {
    "projects": {
        "web": {
            "architect": {
                "build": {
                    "builder": "@angular-devkit/build-angular:application",
                    "options": {"browser": "src/main.ts", "polyfills": ["zone.js", "src/polyfills.ts"]},
                    "configurations": {
                        "production": {
                            "fileReplacements": [
                                {
                                    "replace": "src/environments/environment.ts",
                                    "with": "src/environments/environment.prod.ts",
                                }
                            ]
                        }
                    },
                },
                "test": {
                    "builder": "@angular-devkit/build-angular:karma",
                    "options": {"main": "src/test.ts", "karmaConfig": "karma.conf.js"},
                },
            }
        }
    }
}

_APP = {
    "src/main.ts": (
        "import { platformBrowserDynamic } from '@angular/platform-browser-dynamic';\n"
        "import { AppModule } from './app/app.module';\n"
        "platformBrowserDynamic().bootstrapModule(AppModule);\n"
    ),
    "src/polyfills.ts": "import 'zone.js';\n",
    "src/test.ts": "import 'zone.js/testing';\n",
    "karma.conf.js": "module.exports = function (config) {};\n",
    "src/environments/environment.ts": "export const environment = { production: false };\n",
    "src/environments/environment.prod.ts": "export const environment = { production: true };\n",
    "src/app/app.module.ts": (
        "import { NgModule } from '@angular/core';\n"
        "import { RouterModule, Routes } from '@angular/router';\n"
        "import { HomeComponent } from './home.component';\n"
        "const requireAuth = () => true;\n"
        "const lazy = () => import('./users.module').then(m => m.UsersModule);\n"
        "const routes: Routes = [\n"
        "  { path: '', component: HomeComponent, canActivate: [requireAuth] },\n"
        "  { path: 'users', loadChildren: lazy },\n"
        "];\n"
        "@NgModule({ imports: [RouterModule.forRoot(routes)], declarations: [HomeComponent] })\n"
        "export class AppModule {}\n"
        "function neverUsed() { return 1; }\n"
    ),
    "src/app/home.component.ts": (
        "import { Component } from '@angular/core';\n"
        "import { environment } from '../environments/environment';\n"
        "@Component({ selector: 'app-home', template: '' })\n"
        "export class HomeComponent { prod = environment.production; }\n"
    ),
    "src/app/users.module.ts": (
        "import { NgModule } from '@angular/core';\n@NgModule({})\nexport class UsersModule {}\n"
    ),
    "src/app/orphan.component.ts": (
        "import { Component } from '@angular/core';\n"
        "@Component({ selector: 'app-orphan', template: '' })\nexport class OrphanComponent {}\n"
    ),
}


def _write(repo: Path, files: dict[str, str]) -> Path:
    for rel, text in files.items():
        path = repo / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return repo


def _graph(repo: Path) -> tuple[object, set[tuple[str, str, str]]]:
    builder = GraphBuilder(repo)
    parser = ASTParser()
    source_map: dict[str, bytes] = {}
    for fi in FileTraverser(repo).traverse():
        src = Path(fi.abs_path).read_bytes()
        builder.add_file(parser.parse_file(fi, src))
        source_map[fi.path] = src
    builder.set_source_map(source_map)
    builder.build()
    builder.add_framework_edges(["Angular"])
    report = DeadCodeAnalyzer(
        builder.graph(), {}, parsed_files=builder._parsed_files, source_map=source_map, repo_root=repo
    ).analyze({"detect_unused_internals": True})
    dead = {(f.kind.value, f.file_path, f.symbol_name or "") for f in report.findings}
    return builder.graph(), dead


class TestReferences:
    def test_names_in_metadata_router_config_and_bootstrap(self) -> None:
        text = (
            "bootstrapApplication(AppComponent, appConfig);\n"
            "export const routes: Routes = [{ path: 'a', component: A, canActivate: [guard] },"
            " { path: 'b', component: mobile ? Small : Large }, ...childRoutes];\n"
            "@Component({ selector: 'x', imports: [Child, 'NotAName'] /* Old */ })\n"
            "export class Own {}\n"
        )
        assert sorted(set(angular_references(text))) == [
            ("A", False),
            ("AppComponent", True),
            ("Child", False),
            ("Large", False),
            ("Small", False),
            ("appConfig", True),
            ("childRoutes", False),
            ("guard", False),
            ("mobile", False),
        ]

    def test_a_site_in_a_comment_or_string_is_none(self) -> None:
        text = (
            "// bootstrapApplication(Old)\nconst s = 'bootstrapApplication(Fake)';\n"
            "@NgModule({\n  // don't forget\n  declarations: [AppComponent],\n"
            "  providers: [{ provide: 'x', useValue: 'Helper' }] })\nexport class M {}\n"
        )
        assert sorted(set(angular_references(text))) == [("AppComponent", False)]

    def test_a_class_naming_itself_in_its_metadata_is_not_a_use(self) -> None:
        text = (
            "@Component({ providers: [{ provide: X, useExisting: forwardRef(() => Self) }] })\n"
            "export class Self {}\n"
        )
        assert sorted(set(angular_references(text))) == [("X", False), ("forwardRef", False)]

    def test_a_file_with_no_site_reads_nothing(self) -> None:
        assert list(angular_references("const routes = [];\nfoo(Bar);\n")) == []


class TestEntryFiles:
    def test_build_and_test_entries_and_file_replacements(self) -> None:
        paths = {"src/main.ts", "src/polyfills.ts", "src/test.ts", "karma.conf.js",
                 "src/environments/environment.prod.ts", "src/app/app.module.ts"}  # fmt: skip
        assert angular_entry_files("angular.json", _ANGULAR_JSON, paths) == [
            "karma.conf.js",
            "src/environments/environment.prod.ts",
            "src/main.ts",
            "src/polyfills.ts",
            "src/test.ts",
        ]

    def test_nx_paths_are_workspace_relative_and_other_toolchains_are_skipped(self) -> None:
        project = {
            "targets": {
                "build": {"executor": "@nx/angular:application", "options": {"main": "apps/web/src/main.ts"}},
                "serve-api": {"executor": "@nx/node:build", "options": {"main": "apps/web/server.ts"}},
            }
        }
        paths = {"apps/web/src/main.ts", "apps/web/server.ts"}
        assert angular_entry_files("apps/web/project.json", project, paths) == ["apps/web/src/main.ts"]

    def test_an_angular_json_in_a_subdirectory_resolves_against_it(self) -> None:
        config = {
            "projects": {
                "web": {
                    "architect": {
                        "build": {
                            "builder": "@angular/build:application",
                            "options": {
                                "browser": "src/main.ts",
                                "ssr": {"entry": "src/server.ts"},
                                "scripts": ["src/legacy.js", {"input": "src/lazy.js", "inject": False}],
                            },
                        }
                    }
                }
            }
        }
        paths = {"frontend/src/main.ts", "frontend/src/server.ts", "frontend/src/legacy.js",
                 "frontend/src/lazy.js", "src/main.ts"}  # fmt: skip
        assert angular_entry_files("frontend/angular.json", config, paths) == [
            "frontend/src/lazy.js",
            "frontend/src/legacy.js",
            "frontend/src/main.ts",
            "frontend/src/server.ts",
        ]


def test_an_angular_app_s_convention_files_are_not_dead_code(tmp_path: Path) -> None:
    repo = _write(tmp_path, {**_APP, "angular.json": json.dumps(_ANGULAR_JSON)})
    graph, dead = _graph(repo)
    for path in ("src/main.ts", "src/polyfills.ts", "src/test.ts", "karma.conf.js",
                 "src/environments/environment.prod.ts"):  # fmt: skip
        assert graph.has_edge("framework:angular", path), path
    assert graph.get_edge_data("src/app/app.module.ts::__module__", "src/app/app.module.ts::routes")[
        "edge_type"
    ] == "framework_binds"
    flagged = {(kind, path, name) for kind, path, name in dead if kind != "zombie_package"}
    # Only what nothing names: the orphan component and the unused function.
    assert flagged == {
        ("unreachable_file", "src/app/orphan.component.ts", ""),
        ("unused_export", "src/app/orphan.component.ts", "OrphanComponent"),
        ("unused_internal", "src/app/app.module.ts", "neverUsed"),
    }


def test_a_commented_workspace_config_with_a_bom_is_read(tmp_path: Path) -> None:
    config = "\ufeff// generated\n" + json.dumps(_ANGULAR_JSON).replace('"projects"', '/* x */ "projects"', 1)
    repo = _write(tmp_path, {**_APP, "angular.json": config})
    graph, _dead = _graph(repo)
    assert graph.has_edge("framework:angular", "src/polyfills.ts")


def test_without_a_workspace_config_the_bootstrap_file_is_still_an_entry(tmp_path: Path) -> None:
    repo = _write(tmp_path, {k: v for k, v in _APP.items() if k.startswith("src/")})
    graph, _dead = _graph(repo)
    assert graph.has_edge("framework:angular", "src/main.ts")
    assert not graph.has_edge("framework:angular", "src/polyfills.ts")
