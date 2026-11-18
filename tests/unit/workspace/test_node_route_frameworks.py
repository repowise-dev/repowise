"""Fastify, hapi, Koa router prefixes and Next.js ``pages/api`` as HTTP providers.

Fastify and Koa routes use the Express call shape, so they were read, but at the
wrong path: Fastify serves a plugin's routes under the prefix its ``register``
call gives it, and a Koa router carries its own ``prefix``. hapi declares a route
as an object literal and Next.js Pages Router takes it from the file's location,
so neither produced a contract at all.
"""

from __future__ import annotations

from pathlib import Path

from repowise.core.ingestion.framework_routes import express_routes
from repowise.core.ingestion.framework_routes_next_pages import (
    next_pages_api_path,
    next_pages_api_verbs,
)
from repowise.core.workspace.extractors.http import HttpExtractor
from repowise.core.workspace.origins import ContractOrigin


def _write(root: Path, files: dict[str, str]) -> Path:
    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return root


def _providers(root: Path) -> dict[str, dict]:
    return {
        c.contract_id: {"framework": c.meta["framework"], "file": c.file_path, "origin": c.origin}
        for c in HttpExtractor().extract(root, "api")
        if c.role == "provider"
    }


# ---------------------------------------------------------------------------
# Fastify
# ---------------------------------------------------------------------------

# The shape of a real gateway server: typed plugins in their own modules, each
# registered with a prefix, one of them registering a sub-plugin of its own.
FASTIFY_SERVER_TS = """\
import Fastify from 'fastify';
import {opsRoutes} from './ops';
import {folderRoutes} from './folders';
import {browserRoutes} from './browser';

export async function buildServer() {
\tconst app = Fastify({logger: false});
\tawait app.register(opsRoutes);
\tawait app.register(folderRoutes, {prefix: '/api/folders'});
\t// await app.register(legacyRoutes, {prefix: '/old'});
\tawait app.register(browserRoutes, {prefix: '/api/browser'});
\treturn app;
}
"""

FASTIFY_OPS_TS = """\
import type {FastifyPluginAsync} from 'fastify';

export const opsRoutes: FastifyPluginAsync = async (app) => {
\tapp.get('/healthz', async () => ({ok: true}));
};
"""

FASTIFY_FOLDERS_TS = """\
import type {FastifyPluginAsync} from 'fastify';

export const folderRoutes: FastifyPluginAsync = async (app) => {
\tapp.get('/', async () => ({folders: []}));
\tapp.post<{
\t\tBody: {path: string};
\t}>(
\t\t'/local',
\t\tasync (req, reply) => reply.send({}),
\t);
\tapp.delete<{Params: {idOrName: string}}>('/:idOrName', async () => ({}));
};
"""

FASTIFY_BROWSER_INDEX_TS = """\
import type {FastifyPluginAsync} from 'fastify';
import {browserLaunchRoute} from './launch';

export const browserRoutes: FastifyPluginAsync = async (app) => {
\tawait app.register(browserLaunchRoute);
};
"""

FASTIFY_BROWSER_LAUNCH_TS = """\
import type {FastifyPluginAsync} from 'fastify';

export const browserLaunchRoute: FastifyPluginAsync = async (app) => {
\tapp.post('/launch', async () => ({}));
};
"""


class TestFastify:
    def test_plugin_routes_take_the_register_prefix(self, tmp_path: Path) -> None:
        root = _write(
            tmp_path,
            {
                "src/server.ts": FASTIFY_SERVER_TS,
                "src/ops.ts": FASTIFY_OPS_TS,
                "src/folders/index.ts": FASTIFY_FOLDERS_TS,
                "src/browser/index.ts": FASTIFY_BROWSER_INDEX_TS,
                "src/browser/launch.ts": FASTIFY_BROWSER_LAUNCH_TS,
            },
        )
        found = _providers(root)
        assert set(found) == {
            "http::GET::/healthz",
            "http::GET::/api/folders",
            "http::POST::/api/folders/local",
            "http::DELETE::/api/folders/{param}",
            # A sub-plugin registered with no prefix inherits its parent's.
            "http::POST::/api/browser/launch",
        }
        assert {c["framework"] for c in found.values()} == {"fastify"}
        assert {c["origin"] for c in found.values()} == {ContractOrigin.ROUTE_REGISTRATION.value}

    def test_route_objects_and_require_plugins(self, tmp_path: Path) -> None:
        root = _write(
            tmp_path,
            {
                "app.js": (
                    "const fastify = require('fastify')({ logger: true });\n"
                    "fastify.register(require('./routes/users'), { prefix: '/v1' });\n"
                    "fastify.route({ method: ['GET', 'HEAD'], url: '/status', handler: s });\n"
                ),
                "routes/users.js": (
                    "module.exports = async function (fastify, opts) {\n"
                    "  fastify.route({ method: 'POST', url: '/users', handler: create });\n"
                    "  fastify.get('/users/:id', { schema }, show);\n"
                    "};\n"
                ),
            },
        )
        assert set(_providers(root)) == {
            "http::GET::/status",
            "http::POST::/v1/users",
            "http::GET::/v1/users/{param}",
        }

    def test_chained_registers_options_variable_and_constant_urls(self, tmp_path: Path) -> None:
        # A RealWorld Fastify shape: one options object for a chain of
        # registers, a prefix and urls built from constants.
        root = _write(
            tmp_path,
            {
                "src/utils/server.ts": (
                    "import Fastify from 'fastify';\n"
                    "import userRoutes from '../modules/user';\n"
                    "const API_PREFIX = 'api';\n"
                    "export async function build() {\n"
                    "  const fastify = Fastify();\n"
                    "  const _options = { prefix: API_PREFIX };\n"
                    "  fastify\n"
                    "    .register(cors, { origin: true })\n"
                    "    .register(userRoutes, _options);\n"
                    "  return fastify;\n"
                    "}\n"
                ),
                "src/modules/user/index.ts": (
                    "import { FastifyInstance } from 'fastify';\n"
                    "export default async function (fastify: FastifyInstance) {\n"
                    "  const apiModuleUsers = '/users';\n"
                    "  fastify.route({ method: 'POST', url: `${apiModuleUsers}/login`, handler: h });\n"
                    "  fastify.route({ method: 'GET', url: `${unknown}/x`, handler: h });\n"
                    "}\n"
                ),
            },
        )
        assert set(_providers(root)) == {"http::POST::/api/users/login"}

    def test_inline_plugins_nest_their_prefixes(self, tmp_path: Path) -> None:
        root = _write(
            tmp_path,
            {
                "server.js": (
                    "const app = require('fastify')();\n"
                    "app.register(async (api) => {\n"
                    "  api.get('/items', list);\n"
                    "  api.register(async function (v2) {\n"
                    "    v2.get('/items', list2);\n"
                    "  }, { prefix: '/v2' });\n"
                    "}, { prefix: '/api' });\n"
                ),
            },
        )
        assert set(_providers(root)) == {"http::GET::/api/items", "http::GET::/api/v2/items"}

    def test_fastify_plugin_wrapper_ignores_the_prefix(self, tmp_path: Path) -> None:
        # fastify-plugin breaks encapsulation, and with it the prefix option.
        root = _write(
            tmp_path,
            {
                "app.js": (
                    "const app = require('fastify')();\n"
                    "app.register(require('./shared'), { prefix: '/ignored' });\n"
                ),
                "shared.js": (
                    "const fp = require('fastify-plugin');\n"
                    "module.exports = fp(async function (fastify) {\n"
                    "  fastify.get('/ping', ping);\n"
                    "});\n"
                ),
            },
        )
        assert set(_providers(root)) == {"http::GET::/ping"}

    def test_a_prefix_it_cannot_read_drops_the_routes(self, tmp_path: Path) -> None:
        root = _write(
            tmp_path,
            {
                "app.js": (
                    "const app = require('fastify')();\n"
                    "app.register(require('./users'), { prefix: API_BASE });\n"
                ),
                "users.js": (
                    "module.exports = async function (fastify) { fastify.get('/users', h); };\n"
                ),
            },
        )
        assert _providers(root) == {}

    def test_two_prefixes_in_one_file_drop_the_routes(self, tmp_path: Path) -> None:
        root = _write(
            tmp_path,
            {
                "app.js": (
                    "const app = require('fastify')();\n"
                    "const p = require('./p');\n"
                    "app.register(p, { prefix: '/a' });\n"
                    "app.register(p, { prefix: '/b' });\n"
                ),
                "p.js": "module.exports = async function (fastify) { fastify.get('/x', h); };\n",
            },
        )
        assert _providers(root) == {}

    def test_a_plugin_registered_through_an_alias_is_not_guessed(self, tmp_path: Path) -> None:
        root = _write(
            tmp_path,
            {
                "src/app.ts": (
                    "import Fastify from 'fastify';\nimport routes from '@/routes';\n"
                    "const app = Fastify();\napp.register(routes, { prefix: '/api' });\n"
                ),
                "src/routes.ts": (
                    "import type { FastifyInstance } from 'fastify';\n"
                    "export default async function (app: FastifyInstance) { app.get('/u', h); }\n"
                ),
            },
        )
        assert _providers(root) == {}

    def test_a_plugin_module_without_a_fastify_import(self, tmp_path: Path) -> None:
        # Registered from a Fastify file, so it is Fastify's, not Express's.
        root = _write(
            tmp_path,
            {
                "app.js": (
                    "const app = require('fastify')();\n"
                    "app.register(require('./users'), { prefix: '/v1' });\n"
                ),
                "users.js": "module.exports = async function (app) { app.get('/users', h); };\n",
            },
        )
        assert _providers(root) == {"http::GET::/v1/users": {
            "framework": "fastify", "file": "users.js",
            "origin": ContractOrigin.ROUTE_REGISTRATION.value,
        }}

    def test_express_files_stay_with_the_express_dialect(self, tmp_path: Path) -> None:
        root = _write(
            tmp_path,
            {
                "server.js": (
                    "// not 'fastify' or koa-router\n"
                    "const supported = ['fastify', 'koa'];\n"
                    "const app = express();\napp.get('/users', h);\n"
                ),
            },
        )
        assert _providers(root)["http::GET::/users"]["framework"] == "express"

    def test_plugin_scan_is_linear_on_a_large_flat_table(self) -> None:
        # A data table in a Fastify file once cost seconds in the arrow scan.
        import time

        from repowise.core.ingestion.framework_routes_fastify import fastify_plugins

        content = "import Fastify from 'fastify';\n" + "  key: 'value',\n" * 20000
        start = time.perf_counter()
        fastify_plugins(content, {"key", "app"})
        assert time.perf_counter() - start < 2.0

    def test_type_argument_between_verb_and_call(self) -> None:
        (route,) = list(express_routes("app.get<{Params: {id: string}}>('/x/:id', h)"))
        assert route.path == "/x/:id"


# ---------------------------------------------------------------------------
# hapi
# ---------------------------------------------------------------------------


class TestHapi:
    def test_server_route_objects(self, tmp_path: Path) -> None:
        root = _write(
            tmp_path,
            {
                "server.js": (
                    "const Hapi = require('@hapi/hapi');\n"
                    "const server = Hapi.server({ port: 3000 });\n"
                    "server.route({ method: 'GET', path: '/accounts/{id}', handler: get });\n"
                    "server.route([\n"
                    "  { method: ['PUT', 'PATCH'], path: '/accounts/{id}', options: { handler: put } },\n"
                    "  { method: '*', path: '/fallback', handler: any },\n"
                    "]);\n"
                ),
            },
        )
        found = _providers(root)
        assert set(found) == {
            "http::GET::/accounts/{param}",
            "http::PUT::/accounts/{param}",
            "http::PATCH::/accounts/{param}",
            "http::*::/fallback",
        }
        assert {c["framework"] for c in found.values()} == {"hapi"}

    def test_plugin_prefix_and_route_table_modules(self, tmp_path: Path) -> None:
        root = _write(
            tmp_path,
            {
                "server.js": (
                    "const Hapi = require('@hapi/hapi');\n"
                    "const server = Hapi.server();\n"
                    "await server.register({ plugin: require('./api'), routes: { prefix: '/api' } });\n"
                ),
                "api/index.js": (
                    "exports.plugin = {\n"
                    "  name: 'api',\n"
                    "  register: async (server) => { server.route(require('./orders')); },\n"
                    "};\n"
                ),
                # A plain route table: no hapi import, read because it is mounted.
                "api/orders.js": (
                    "module.exports = [\n"
                    "  { method: 'GET', path: '/orders', handler: list },\n"
                    "  { method: 'POST', path: '/orders', handler: create },\n"
                    "];\n"
                ),
            },
        )
        assert set(_providers(root)) == {"http::GET::/api/orders", "http::POST::/api/orders"}

    def test_optional_param(self, tmp_path: Path) -> None:
        root = _write(tmp_path, {"server.js": (
            "const server = require('@hapi/hapi').server();\n"
            "server.route({ method: 'GET', path: '/users/{id?}', handler: h });\n"
        )})
        assert set(_providers(root)) == {"http::GET::/users/{param}"}

    def test_prefix_in_the_register_options(self, tmp_path: Path) -> None:
        root = _write(
            tmp_path,
            {
                "server.js": (
                    "const Hapi = require('@hapi/hapi');\nconst server = Hapi.server();\n"
                    "server.register({ plugin: require('./api') }, { routes: { prefix: '/v1' } });\n"
                ),
                "api.js": (
                    "exports.plugin = { register: async (server) => {\n"
                    "  server.route({ method: 'GET', path: '/u', handler: h });\n} };\n"
                ),
            },
        )
        assert set(_providers(root)) == {"http::GET::/v1/u"}

    def test_route_table_factory_module(self, tmp_path: Path) -> None:
        # hapi 16 style: a plugin passes `Routes(server)` to `server.route`.
        root = _write(
            tmp_path,
            {
                "lib/users/index.js": (
                    "const Routes = require('./routes');\n"
                    "const register = (server, options, next) => {\n"
                    "  server.route(Routes(server));\n  next();\n};\n"
                    "module.exports = register;\n"
                ),
                "lib/users/routes.js": (
                    "module.exports = (server) => {\n"
                    "  const handlers = require('./handlers')(server);\n"
                    "  return [\n"
                    "    { method: 'GET', path: '/user', config: { auth: 'jwt' }, handler: handlers.get },\n"
                    "  ];\n};\n"
                ),
            },
        )
        assert set(_providers(root)) == {"http::GET::/user"}

    def test_request_options_are_not_routes(self, tmp_path: Path) -> None:
        # Node's http.request options carry method and path but no handler.
        root = _write(
            tmp_path,
            {
                "client.js": (
                    "const server = require('@hapi/hapi').server();\n"
                    "http.request({ method: 'GET', path: '/remote', host: 'x' });\n"
                    "// server.route({ method: 'GET', path: '/commented', handler: h });\n"
                ),
                "table.js": "module.exports = [{ method: 'GET', path: '/unmounted', handler: h }];\n",
            },
        )
        assert _providers(root) == {}


# ---------------------------------------------------------------------------
# Koa
# ---------------------------------------------------------------------------


class TestKoa:
    def test_router_prefix_is_served(self, tmp_path: Path) -> None:
        root = _write(
            tmp_path,
            {
                "server.js": (
                    "const Router = require('@koa/router');\n"
                    "const router = new Router({ prefix: '/api' });\n"
                    "const carts = new Router();\n"
                    "carts.prefix('/carts');\n"
                    "router.get('/health', h);\n"
                    "carts.get('/:id', show);\n"
                    "carts.post('/', create);\n"
                ),
            },
        )
        found = _providers(root)
        assert set(found) == {
            "http::GET::/api/health",
            "http::GET::/carts/{param}",
            "http::POST::/carts",
        }
        assert {c["framework"] for c in found.values()} == {"koa"}

    def test_router_modules_nested_under_a_prefixed_router(self, tmp_path: Path) -> None:
        # The RealWorld Koa layout: route modules export `router.routes()`, an
        # index nests them in `api`, and `api` is served under `/api`.
        root = _write(
            tmp_path,
            {
                "src/routes/index.js": (
                    "const Router = require('koa-router');\n"
                    "const router = new Router();\n"
                    "const api = new Router();\n"
                    "const users = require('./users-router');\n"
                    "api.use(users);\n"
                    "api.use('/v2', require('./tags-router'));\n"
                    "router.use('/api', api.routes());\n"
                    "module.exports = router;\n"
                ),
                "src/routes/users-router.js": (
                    "const Router = require('koa-router');\n"
                    "const router = new Router();\n"
                    "router.get('/user', h);\n"
                    "router.del('/users/:id', auth, remove);\n"
                    "module.exports = router.routes();\n"
                ),
                "src/routes/tags-router.js": (
                    "import Router from '@koa/router';\n"
                    "const router = new Router({ prefix: '/tags' });\n"
                    "router.get('/', list);\n"
                    "export default router.routes();\n"
                ),
            },
        )
        assert set(_providers(root)) == {
            "http::GET::/api/user",
            "http::DELETE::/api/users/{param}",
            "http::GET::/api/v2/tags",
        }

    def test_wrapped_or_twice_placed_routers_are_not_guessed(self, tmp_path: Path) -> None:
        root = _write(
            tmp_path,
            {
                "server.js": (
                    "const Router = require('@koa/router');\n"
                    "const r = new Router();\nconst c = new Router();\n"
                    "const a = new Router({ prefix: '/a' });\nconst b = new Router({ prefix: '/b' });\n"
                    "app.use(mount('/api', r.routes()));\n"
                    "a.use(c.routes());\nb.use(c.routes());\n"
                    "r.get('/x', h);\nc.get('/y', h);\na.get('/z', h);\n"
                ),
            },
        )
        assert set(_providers(root)) == {"http::GET::/a/z"}

    def test_a_prefix_it_cannot_read_drops_the_routes(self, tmp_path: Path) -> None:
        root = _write(
            tmp_path,
            {
                "server.js": (
                    "const Router = require('@koa/router');\n"
                    "const router = new Router({ prefix: config.base });\n"
                    "router.get('/x', h);\n"
                ),
            },
        )
        assert _providers(root) == {}


# ---------------------------------------------------------------------------
# Next.js pages/api
# ---------------------------------------------------------------------------


class TestNextPagesApi:
    def test_path_from_location(self) -> None:
        assert next_pages_api_path("pages/api/users/[id].ts") == "/api/users/{id}"
        assert next_pages_api_path("src/pages/api/posts/index.js") == "/api/posts"
        assert next_pages_api_path("pages/api/docs/[[...slug]].tsx") == "/api/docs/{slug}"
        assert next_pages_api_path("pages/users/[id].tsx") is None  # a page, not an API route
        assert next_pages_api_path("pages/api/users.test.ts") is None
        assert next_pages_api_path("pages/api/readme.md") is None
        assert next_pages_api_path("pages/api/types.d.ts") is None
        assert next_pages_api_path("pages/api/__tests__/users.ts") is None

    def test_verbs_from_method_checks(self) -> None:
        assert [v for v, _ in next_pages_api_verbs(
            "export default function h(req, res) {\n"
            "  if (req.method !== 'POST') return res.status(405).end();\n}\n"
        )] == ["POST"]
        assert [v for v, _ in next_pages_api_verbs(
            "export default async (req, res) => {\n"
            "  switch (req.method) {\n    case 'GET': break;\n    case 'PUT': break;\n  }\n};\n"
        )] == ["GET", "PUT"]
        assert [v for v, _ in next_pages_api_verbs("module.exports = (req, res) => res.end();\n")] == ["*"]
        assert next_pages_api_verbs("export const config = {};\n") is None
        # A helper module kept under pages/api touches no request or response.
        assert next_pages_api_verbs("export default function slugify(s) { return s; }\n") is None

    def test_contracts(self, tmp_path: Path) -> None:
        root = _write(
            tmp_path,
            {
                "pages/api/users/[id].ts": (
                    "export default function handler(req, res) {\n"
                    "  if (req.method === 'GET') return res.json({});\n"
                    "  if (req.method === 'DELETE') return res.end();\n}\n"
                ),
                "pages/api/health.js": "export default (req, res) => res.end('ok');\n",
                "pages/index.tsx": "export default function Home() { return null; }\n",
            },
        )
        found = _providers(root)
        assert set(found) == {
            "http::GET::/api/users/{param}",
            "http::DELETE::/api/users/{param}",
            "http::*::/api/health",
        }
        assert {c["framework"] for c in found.values()} == {"next-pages"}
