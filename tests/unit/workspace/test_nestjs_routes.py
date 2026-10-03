"""NestJS controllers as HTTP providers.

The served path is the global prefix (unless excluded), the URI version, the
controller prefix and the route's own path, in that order. The prefix and the
versioning are declared in the app's bootstrap, usually another file, so every
test here extracts a whole repo.
"""

from __future__ import annotations

from pathlib import Path

from repowise.core.workspace.extractors.http import HttpExtractor

_COMMON = "import { Controller, Get, Post, Delete, Version } from '@nestjs/common';\n"


def _write(repo: Path, files: dict[str, str]) -> Path:
    for rel, text in files.items():
        path = repo / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return repo


def _routes(repo: Path) -> set[tuple[str, str]]:
    return {
        (c.meta["method"], c.meta["path"])
        for c in HttpExtractor().extract(repo, "api")
        if c.role == "provider" and c.meta.get("framework") == "nestjs"
    }


def _controller(body: str, head: str = "@Controller('cats')") -> str:
    return f"{_COMMON}{head}\nexport class CatsController {{\n{body}\n}}\n"


class TestControllers:
    def test_verbs_under_the_controller_prefix(self, tmp_path: Path) -> None:
        body = (
            "  @Get()\n  findAll() {}\n"
            "  @Get(':id')\n  findOne() {}\n"
            "  @Post()\n  create() {}\n"
            "  @Delete(':id/toys/:toy')\n  remove() {}\n"
        )
        repo = _write(tmp_path, {"src/cats/cats.controller.ts": _controller(body)})
        assert _routes(repo) == {
            ("GET", "/cats"),
            ("GET", "/cats/{param}"),
            ("POST", "/cats"),
            ("DELETE", "/cats/{param}/toys/{param}"),
        }

    def test_path_arrays_object_prefix_and_constants(self, tmp_path: Path) -> None:
        body = "  @Get(['a', PATH])\n  many() {}\n"
        head = "const PATH = 'b';\n@Controller({ path: 'v', host: 'x.io' })"
        repo = _write(tmp_path, {"src/c.controller.ts": _controller(body, head)})
        assert _routes(repo) == {("GET", "/v/a"), ("GET", "/v/b")}

    def test_a_controller_with_no_path_serves_at_the_root(self, tmp_path: Path) -> None:
        body = "  @Get('health')\n  health() {}\n"
        repo = _write(
            tmp_path,
            {
                "a.controller.ts": _controller(body, "@Controller()"),
                "b.controller.ts": _controller(body.replace("health", "ping"), "@Controller({ host: 'h' })"),
            },
        )
        assert _routes(repo) == {("GET", "/health"), ("GET", "/ping")}

    def test_sse_is_a_get_and_all_head_options_are_not_recorded(self, tmp_path: Path) -> None:
        body = "  @Sse('events')\n  s() {}\n  @All('any')\n  a() {}\n  @Head('h')\n  h() {}\n"
        repo = _write(tmp_path, {"c.controller.ts": _controller(body)})
        assert _routes(repo) == {("GET", "/cats/events")}

    def test_stacked_decorators_and_comments_reach_the_handler(self, tmp_path: Path) -> None:
        body = (
            "  @Get(':id')\n  @UseGuards(AuthGuard('jwt'))\n  // cached\n"
            "  @Header('Cache-Control', 'none')\n  async findOne(@Param('id') id: string) {}\n"
        )
        repo = _write(tmp_path, {"c.controller.ts": _controller(body)})
        (row,) = [c for c in HttpExtractor().extract(repo, "api") if c.role == "provider"]
        assert row.meta["handler"] == "CatsController.findOne"
        assert row.line == 4

    def test_an_unreadable_path_is_refused(self, tmp_path: Path) -> None:
        body = "  @Get(routes.one)\n  one() {}\n  @Get('two')\n  two() {}\n"
        repo = _write(
            tmp_path,
            {
                "a.controller.ts": _controller(body),
                "b.controller.ts": _controller("  @Get('x')\n  x() {}\n", "@Controller(prefixFor('b'))"),
            },
        )
        assert _routes(repo) == {("GET", "/cats/two")}

    def test_without_the_nest_import_nothing_is_read(self, tmp_path: Path) -> None:
        text = "@Controller('cats')\nclass C {\n  @Get(':id')\n  one() {}\n}\n"
        repo = _write(tmp_path, {"c.ts": text})
        assert _routes(repo) == set()


class TestGlobalPrefix:
    _MAIN = (
        "import { NestFactory } from '@nestjs/core';\n"
        "async function bootstrap() {\n  const app = await NestFactory.create(AppModule);\n"
        "  app.setGlobalPrefix({args});\n  await app.listen(3000);\n}\n"
    )

    def _app(self, tmp_path: Path, args: str, body: str = "  @Get(':id')\n  one() {}\n") -> Path:
        return _write(
            tmp_path,
            {
                "src/main.ts": self._MAIN.replace("{args}", args),
                "src/cats/cats.controller.ts": _controller(body),
            },
        )

    def test_the_prefix_from_the_bootstrap_file(self, tmp_path: Path) -> None:
        assert _routes(self._app(tmp_path, "'api/v1'")) == {("GET", "/api/v1/cats/{param}")}

    def test_excluded_routes_keep_their_own_path(self, tmp_path: Path) -> None:
        body = "  @Get('health')\n  h() {}\n  @Get(':id')\n  one() {}\n  @Post('health')\n  p() {}\n"
        args = "'api', { exclude: [{ path: 'cats/health', method: RequestMethod.GET }] }"
        assert _routes(self._app(tmp_path, args, body)) == {
            ("GET", "/cats/health"),
            ("GET", "/api/cats/{param}"),
            ("POST", "/api/cats/health"),
        }

    def test_a_wildcard_exclude_covers_everything_below_it(self, tmp_path: Path) -> None:
        body = "  @Get('a')\n  a() {}\n"
        assert _routes(self._app(tmp_path, "'api', { exclude: ['cats/(.*)'] }", body)) == {
            ("GET", "/cats/a")
        }

    def test_an_unread_exclude_list_excludes_nothing(self, tmp_path: Path) -> None:
        assert _routes(self._app(tmp_path, "'api', { exclude: excludePaths }")) == {
            ("GET", "/api/cats/{param}")
        }

    def test_an_unreadable_prefix_refuses_the_app(self, tmp_path: Path) -> None:
        assert _routes(self._app(tmp_path, "config.prefix")) == set()

    def test_each_app_of_a_monorepo_takes_its_own_prefix(self, tmp_path: Path) -> None:
        main = self._MAIN
        repo = _write(
            tmp_path,
            {
                "apps/api/src/main.ts": main.replace("{args}", "'api'"),
                "apps/api/src/cats.controller.ts": _controller("  @Get()\n  a() {}\n"),
                "apps/api-admin/src/main.ts": main.replace("{args}", "'admin'"),
                "apps/api-admin/src/cats.controller.ts": _controller("  @Post()\n  b() {}\n"),
                # A bootstrap of its own with no prefix: nothing inherited.
                "apps/plain/src/main.ts": "const app = await NestFactory.create(M);\n",
                "apps/plain/src/cats.controller.ts": _controller("  @Delete()\n  c() {}\n"),
                # A library shared by apps that disagree is served where this cannot tell.
                "libs/users/src/users.controller.ts": _controller("  @Patch()\n  d() {}\n"),
            },
        )
        assert _routes(repo) == {("GET", "/api/cats"), ("POST", "/admin/cats"), ("DELETE", "/cats")}

    def test_a_library_controller_takes_the_one_apps_prefix(self, tmp_path: Path) -> None:
        repo = _write(
            tmp_path,
            {
                "apps/api/src/main.ts": self._MAIN.replace("{args}", "'api'"),
                "libs/users/src/lib/users.controller.ts": _controller("  @Get()\n  a() {}\n"),
            },
        )
        assert _routes(repo) == {("GET", "/api/cats")}

    def test_two_disagreeing_declarations_refuse_the_app(self, tmp_path: Path) -> None:
        repo = self._app(tmp_path, "'api'")
        _write(repo, {"src/other.ts": "app.setGlobalPrefix('v2');\n"})
        assert _routes(repo) == set()


class TestVersioning:
    def _app(self, tmp_path: Path, versioning: str, controllers: dict[str, str]) -> Path:
        main = f"app.setGlobalPrefix('api');\napp.enableVersioning({versioning});\n"
        return _write(tmp_path, {"src/main.ts": main, **controllers})

    def test_uri_versions_sit_between_the_prefix_and_the_controller(self, tmp_path: Path) -> None:
        body = (
            "  @Get()\n  all() {}\n"
            "  @Version('2')\n  @Get(':id')\n  one() {}\n"
            "  @Get('n')\n  @Version(VERSION_NEUTRAL)\n  n() {}\n"
        )
        head = "@Controller({ path: 'cats', version: ['1', '3'] })"
        repo = self._app(
            tmp_path,
            "{ type: VersioningType.URI }",
            {"src/c.controller.ts": _controller(body, head)},
        )
        assert _routes(repo) == {
            ("GET", "/api/v1/cats"),
            ("GET", "/api/v3/cats"),
            ("GET", "/api/v2/cats/{param}"),
            ("GET", "/api/cats/n"),
        }

    def test_the_default_version_and_a_custom_prefix(self, tmp_path: Path) -> None:
        repo = self._app(
            tmp_path,
            "{ type: VersioningType.URI, prefix: 'version', defaultVersion: '1' }",
            {"src/c.controller.ts": _controller("  @Get()\n  all() {}\n")},
        )
        assert _routes(repo) == {("GET", "/api/version1/cats")}

    def test_a_class_version_decorator(self, tmp_path: Path) -> None:
        text = f"{_COMMON}@Version('4')\n@Controller('cats')\nexport class C {{\n  @Get()\n  a() {{}}\n}}\n"
        repo = self._app(tmp_path, "", {"src/c.controller.ts": text})
        assert _routes(repo) == {("GET", "/api/v4/cats")}

    def test_header_versioning_leaves_paths_alone(self, tmp_path: Path) -> None:
        repo = self._app(
            tmp_path,
            "{ type: VersioningType.HEADER, header: 'X-Version' }",
            {"src/c.controller.ts": _controller("  @Version('2')\n  @Get()\n  a() {}\n")},
        )
        assert _routes(repo) == {("GET", "/api/cats")}


class TestReviewedEdges:
    def test_two_controllers_in_one_file_and_a_commented_route(self, tmp_path: Path) -> None:
        text = (
            f"{_COMMON}@Controller('a')\nexport class A {{\n  // @Get('old')\n  @Get('new')\n  n() {{}}\n}}\n"
            "@Controller('b')\nexport class B {\n  @Get()\n  m() {}\n}\n"
        )
        repo = _write(tmp_path, {"c.controller.ts": text})
        rows = sorted(
            (c.meta["path"], c.meta["handler"])
            for c in HttpExtractor().extract(repo, "api")
            if c.role == "provider"
        )
        assert rows == [("/a/new", "A.n"), ("/b", "B.m")]

    def test_neutral_inside_version_arrays(self, tmp_path: Path) -> None:
        body = "  @Version(['1', VERSION_NEUTRAL])\n  @Get()\n  a() {}\n"
        repo = _write(
            tmp_path,
            {
                "src/main.ts": "app.setGlobalPrefix('api');\napp.enableVersioning({ defaultVersion: [VERSION_NEUTRAL, '2'] });\n",
                "src/c.controller.ts": _controller(body + "  @Get('d')\n  d() {}\n"),
            },
        )
        assert _routes(repo) == {
            ("GET", "/api/v1/cats"),
            ("GET", "/api/cats"),
            ("GET", "/api/cats/d"),
            ("GET", "/api/v2/cats/d"),
        }

    def test_an_unreadable_version_drops_only_its_route(self, tmp_path: Path) -> None:
        body = "  @Version(V)\n  @Get('x')\n  x() {}\n  @Get('y')\n  y() {}\n"
        repo = _write(
            tmp_path,
            {"src/main.ts": "app.enableVersioning({ prefix: false, defaultVersion: '1' });\n", "src/c.controller.ts": _controller(body)},
        )
        assert _routes(repo) == {("GET", "/1/cats/y")}

    def test_versioning_options_in_a_variable_refuse_the_app(self, tmp_path: Path) -> None:
        repo = _write(
            tmp_path,
            {"src/main.ts": "app.enableVersioning(versioningOptions);\n", "src/c.controller.ts": _controller("  @Get()\n  a() {}\n")},
        )
        assert _routes(repo) == set()

    def test_exclude_wildcards_and_methods(self, tmp_path: Path) -> None:
        body = (
            "  @Get()\n  base() {}\n  @Get('x/y')\n  deep() {}\n"
            "  @Post('h')\n  h() {}\n"
        )
        main = "app.setGlobalPrefix('api', { exclude: ['cats/*', { path: 'cats/h', method: RequestMethod.ALL }] });\n"
        repo = _write(tmp_path, {"src/main.ts": main, "src/c.controller.ts": _controller(body)})
        assert _routes(repo) == {("GET", "/api/cats"), ("GET", "/cats/x/y"), ("POST", "/cats/h")}

    def test_a_splat_exclude_covers_the_path_itself(self, tmp_path: Path) -> None:
        main = "app.setGlobalPrefix('api', { exclude: ['cats/{*splat}'] });\n"
        repo = _write(tmp_path, {"src/main.ts": main, "src/c.controller.ts": _controller("  @Get()\n  a() {}\n")})
        assert _routes(repo) == {("GET", "/cats")}

    def test_only_the_disagreeing_app_is_refused(self, tmp_path: Path) -> None:
        repo = _write(
            tmp_path,
            {
                "apps/a/src/main.ts": "app.setGlobalPrefix('api');\n",
                "apps/a/src/other.ts": "app.setGlobalPrefix('v2');\n",
                "apps/a/src/c.controller.ts": _controller("  @Get()\n  a() {}\n"),
                "apps/b/src/main.ts": "app.setGlobalPrefix('b');\n",
                "apps/b/src/c.controller.ts": _controller("  @Post()\n  b() {}\n"),
            },
        )
        assert _routes(repo) == {("POST", "/b/cats")}


def test_a_declared_mount_two_files_disagree_on_reads_as_ambiguous() -> None:
    from repowise.core.workspace.extractors.http.mounts import (
        declare_mount,
        declared_mount,
        merge_mount_maps,
    )

    merged = merge_mount_maps([declare_mount("k", "a"), declare_mount("k", "b"), declare_mount("j", None)])
    assert declared_mount(merged, "k") == (True, None)
    assert declared_mount(merged, "j") == (True, None)
    assert declared_mount(merge_mount_maps([declare_mount("k", "a")]), "k") == (True, "a")
    assert declared_mount(merged, "other") == (False, None)
