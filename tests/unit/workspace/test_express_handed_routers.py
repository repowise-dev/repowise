"""Express routers handed to a module's exported function serve under the
handed router's mount prefix, and never under a guessed one."""

from __future__ import annotations

import textwrap
from pathlib import Path

from repowise.core.workspace.extractors.http import HttpExtractor


def _routes(tmp_path: Path, files: dict[str, str]) -> set[tuple[str, str]]:
    for rel, body in files.items():
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(textwrap.dedent(body), encoding="utf-8")
    return {
        (c.meta["path"], c.file_path)
        for c in HttpExtractor().extract(tmp_path, "backend")
        if c.role == "provider"
    }


def test_inline_require_call_takes_the_mount_prefix(tmp_path: Path) -> None:
    assert _routes(
        tmp_path,
        {
            "app.js": """
                const express = require('express');
                const app = express();
                const api = express.Router();
                require('./routes/users')(api);
                app.use('/api', api);
            """,
            "routes/users.js": """
                module.exports = function (router) {
                  router.get('/users', listUsers);
                };
            """,
        },
    ) == {("/api/users", "routes/users.js")}


def test_es_default_arrow_and_named_exports(tmp_path: Path) -> None:
    assert _routes(
        tmp_path,
        {
            "src/server.ts": """
                import express, { Router } from 'express';
                import registerUsers from './users';
                import * as orders from './orders';
                import { mountAdmin } from './admin';
                const app = express();
                const api = Router();
                const v1 = Router();
                registerUsers(api);
                orders.register(deps, v1);
                mountAdmin(api);
                api.use('/v1', v1);
                app.use('/api', api);
            """,
            "src/users.ts": """
                export default async (router: Router): Promise<void> => {
                  router.get('/users', list);
                };
            """,
            "src/orders.ts": """
                export function register(deps, router) {
                  router.get('/orders', list);
                }
            """,
            "src/admin.js": """
                function mountAdmin(app) {
                  app.get('/admin', show);
                }
                module.exports = { mountAdmin };
            """,
        },
    ) == {
        ("/api/users", "src/users.ts"),
        ("/api/v1/orders", "src/orders.ts"),
        ("/api/admin", "src/admin.js"),
    }


def test_chains_through_handed_functions_and_module_mounts(tmp_path: Path) -> None:
    # server hands `api` to routes/index, which hands it on to users and
    # mounts an admin router module on it.
    assert _routes(
        tmp_path,
        {
            "server.js": """
                const express = require('express');
                const app = express();
                const api = express.Router();
                require('./routes')(api);
                app.use('/api', api);
            """,
            "routes/index.js": """
                const { Router } = require('express');
                const users = require('./users');
                module.exports = (router) => {
                  users(router);
                  router.use('/admin', require('./admin'));
                  const me = Router();
                  me.get('/me', show);
                  router.use('/account', me);
                };
            """,
            "routes/users.js": """
                module.exports = function register(router) {
                  router.get('/users', list);
                };
            """,
            "routes/admin.js": """
                const router = require('express').Router();
                router.get('/stats', stats);
                module.exports = router;
            """,
        },
    ) == {
        ("/api/users", "routes/users.js"),
        ("/api/admin/stats", "routes/admin.js"),
        ("/api/account/me", "routes/index.js"),
    }


def test_app_handed_at_the_root_keeps_its_path(tmp_path: Path) -> None:
    assert _routes(
        tmp_path,
        {
            "app.js": """
                const app = require('express')();
                require('./routes')(app);
            """,
            "routes.js": """
                module.exports = (app) => { app.get('/users', list); };
            """,
        },
    ) == {("/users", "routes.js")}


def test_two_prefixes_keep_the_in_function_path(tmp_path: Path) -> None:
    assert _routes(
        tmp_path,
        {
            "app.js": """
                const express = require('express');
                const app = express();
                const v1 = express.Router();
                const v2 = express.Router();
                require('./users')(v1);
                require('./users')(v2);
                app.use('/v1', v1);
                app.use('/v2', v2);
            """,
            "users.js": """
                module.exports = function (router) { router.get('/users', list); };
            """,
        },
    ) == {("/users", "users.js")}


def test_chain_top_never_handed_keeps_the_in_function_path(tmp_path: Path) -> None:
    # routes/index's own router comes from nowhere this can read.
    assert _routes(
        tmp_path,
        {
            "routes/index.js": """
                module.exports = (router) => {
                  const api = require('express').Router();
                  require('./users')(api);
                  router.use('/api', api);
                };
            """,
            "routes/users.js": """
                module.exports = function (router) { router.get('/users', list); };
            """,
        },
    ) == {("/users", "routes/users.js")}


def test_test_code_call_sites_are_ignored(tmp_path: Path) -> None:
    assert _routes(
        tmp_path,
        {
            "src/users.js": """
                module.exports = function (router) { router.get('/users', list); };
            """,
            "tests/users.test.js": """
                const express = require('express');
                const app = express();
                const router = express.Router();
                require('../src/users')(router);
                app.use('/under-test', router);
            """,
        },
    ) == {("/users", "src/users.js")}


def test_rebound_or_shadowed_parameter_keeps_the_in_function_path(tmp_path: Path) -> None:
    assert _routes(
        tmp_path,
        {
            "app.js": """
                const express = require('express');
                const app = express();
                const api = express.Router();
                require('./a')(api);
                require('./b')(api);
                app.use('/api', api);
            """,
            "a.js": """
                module.exports = function (router) {
                  router = require('express').Router();
                  router.get('/a', h);
                };
            """,
            "b.js": """
                module.exports = function (router) {
                  [1].forEach(function (router) { router.get('/b', h); });
                };
            """,
        },
    ) == {("/a", "a.js"), ("/b", "b.js")}


def test_cycle_adds_nothing(tmp_path: Path) -> None:
    assert _routes(
        tmp_path,
        {
            "a.js": """
                module.exports = (router) => { require('./b')(router); };
            """,
            "b.js": """
                module.exports = (router) => {
                  require('./a')(router);
                  router.get('/x', h);
                };
            """,
        },
    ) == {("/x", "b.js")}


def test_module_mount_inside_an_unhanded_function_keeps_its_prefix(tmp_path: Path) -> None:
    # A loader the extractor cannot read calls routes/index; the router module
    # it mounts still serves under its own mount, as before.
    assert _routes(
        tmp_path,
        {
            "app.js": """
                const app = require('express')();
                fs.readdirSync('./routes').forEach(f => require('./routes/' + f)(app));
            """,
            "routes/index.js": """
                module.exports = function (app) { app.use('/users', require('./users')); };
            """,
            "routes/users.js": """
                const router = require('express').Router();
                router.get('/list', h);
                module.exports = router;
            """,
        },
    ) == {("/users/list", "routes/users.js")}


def test_function_exported_under_two_names_at_two_prefixes(tmp_path: Path) -> None:
    assert _routes(
        tmp_path,
        {
            "app.js": """
                const express = require('express');
                const app = express();
                const a = express.Router();
                const b = express.Router();
                require('./users')(a);
                require('./users').register(b);
                app.use('/a', a);
                app.use('/b', b);
            """,
            "users.js": """
                function register(router) { router.get('/users', h); }
                module.exports = register;
                module.exports.register = register;
            """,
        },
    ) == {("/users", "users.js")}


def test_spread_arguments_hand_no_prefix(tmp_path: Path) -> None:
    assert _routes(
        tmp_path,
        {
            "app.js": """
                const express = require('express');
                const app = express();
                const api = express.Router();
                require('./users')(...deps, api);
                app.use('/api', api);
            """,
            "users.js": """
                module.exports = function (router, deps) { router.get('/users', h); };
            """,
        },
    ) == {("/users", "users.js")}


def test_module_mount_under_an_ambiguous_handed_router_keeps_its_prefix(tmp_path: Path) -> None:
    assert _routes(
        tmp_path,
        {
            "app.js": """
                const express = require('express');
                const app = express();
                const a = express.Router();
                const b = express.Router();
                require('./routes/index')(a);
                require('./routes/index')(b);
                app.use('/a', a);
                app.use('/b', b);
            """,
            "routes/index.js": """
                module.exports = function (app) { app.use('/x', require('./users')); };
            """,
            "routes/users.js": """
                const router = require('express').Router();
                router.get('/list', h);
                module.exports = router;
            """,
        },
    ) == {("/x/list", "routes/users.js")}


def test_a_spread_call_beside_a_readable_one_keeps_the_in_function_path(tmp_path: Path) -> None:
    assert _routes(
        tmp_path,
        {
            "app.js": """
                const express = require('express');
                const app = express();
                const x = express.Router();
                require('./u')(x);
                require('./u')(...[y]);
                app.use('/x', x);
            """,
            "u.js": """
                module.exports = function (router) { router.get('/u', h); };
            """,
        },
    ) == {("/u", "u.js")}


def test_a_comma_inside_a_type_keeps_parameter_positions(tmp_path: Path) -> None:
    assert _routes(
        tmp_path,
        {
            "src/app.ts": """
                import express, { Router } from 'express';
                import register from './users';
                const app = express();
                const api = Router();
                register(cfg, api);
                app.use('/api', api);
            """,
            "src/users.ts": """
                export default function (opts: Record<string, string>, router: Router) {
                  router.get('/users', list);
                }
            """,
        },
    ) == {("/api/users", "src/users.ts")}
