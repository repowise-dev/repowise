"""Go routes registered in a function that is handed its router group.

``articles.Register(v1.Group("/articles"))`` in ``main.go`` serves the routes
``func Register(router *gin.RouterGroup)`` declares under ``/api/articles``;
before, the route was stored at its in-function path (``/feed``), which no
request log or consumer call names. The shapes are cut from the gin realworld
example app and gin's group-routes example.
"""

from __future__ import annotations

from pathlib import Path

from repowise.core.workspace.extractors.http import HttpExtractor
from repowise.core.workspace.extractors.http.go_register import served_prefixes

GIN = '"github.com/gin-gonic/gin"'

MAIN = f"""\
package main

import (
	{GIN}

	"example.com/app/articles"
	"example.com/app/users"
)

func main() {{
	r := gin.Default()
	v1 := r.Group("/api")
	users.UsersRegister(v1.Group("/users"))
	articles.ArticlesRegister(v1.Group("/articles"))
	r.Run()
}}
"""

ARTICLES = f"""\
package articles

import {GIN}

func ArticlesRegister(router *gin.RouterGroup) {{
	router.GET("/feed", ArticleFeed)
	router.POST("", ArticleCreate)
	router.DELETE("/:slug/comments/:id", ArticleCommentDelete)
}}
"""

USERS = f"""\
package users

import {GIN}

func UsersRegister(router *gin.RouterGroup) {{
	router.POST("/login", UsersLogin)
}}
"""


def _write(root: Path, files: dict[str, str]) -> None:
    files = {"go.mod": "module example.com/app\n", **files}
    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")


def _routes(root: Path) -> list[tuple[str, str, int]]:
    return sorted(
        (c.contract_id, c.file_path, c.line)
        for c in HttpExtractor().extract(root, "app")
        if c.role == "provider"
    )


def test_a_group_handed_to_another_package_prefixes_its_routes(tmp_path: Path) -> None:
    _write(
        tmp_path,
        {"hello.go": MAIN, "articles/routers.go": ARTICLES, "users/routers.go": USERS},
    )
    assert _routes(tmp_path) == [
        ("http::DELETE::/api/articles/{param}/comments/{param}", "articles/routers.go", 8),
        ("http::GET::/api/articles/feed", "articles/routers.go", 6),
        # An empty path under a mounted group is the group's own route.
        ("http::POST::/api/articles", "articles/routers.go", 7),
        ("http::POST::/api/users/login", "users/routers.go", 6),
    ]


def test_a_function_registered_under_two_groups_serves_both(tmp_path: Path) -> None:
    routes = f"""\
package routes

import {GIN}

var router = gin.Default()

func getRoutes() {{
	v1 := router.Group("/v1")
	addPingRoutes(v1)
	v2 := router.Group("/v2")
	addPingRoutes(v2)
}}
"""
    ping = f"""\
package routes

import {GIN}

func addPingRoutes(rg *gin.RouterGroup) {{
	ping := rg.Group("/ping")
	ping.GET("/", func(c *gin.Context) {{}})
}}
"""
    _write(tmp_path, {"routes/main.go": routes, "routes/ping.go": ping})
    assert _routes(tmp_path) == [
        ("http::GET::/v1/ping", "routes/ping.go", 7),
        ("http::GET::/v2/ping", "routes/ping.go", 7),
    ]


def test_a_parameter_handed_on_composes_its_callers_prefix(tmp_path: Path) -> None:
    main = f"""\
package main

import (
	{GIN}
	"example.com/app/api"
)

func main() {{
	r := gin.New()
	api.Setup(r.Group("/api"))
}}
"""
    setup = f"""\
package api

import (
	{GIN}
	"example.com/app/api/users"
)

func Setup(rg *gin.RouterGroup) {{
	users.Register(rg.Group("/users"))
}}
"""
    users = f"""\
package users

import {GIN}

func Register(g *gin.RouterGroup) {{
	g.GET("/:id", Get)
}}
"""
    _write(tmp_path, {"main.go": main, "api/setup.go": setup, "api/users/users.go": users})
    assert _routes(tmp_path) == [("http::GET::/api/users/{param}", "api/users/users.go", 6)]


def test_a_test_mounting_the_function_adds_no_prefix(tmp_path: Path) -> None:
    test = f"""\
package articles

import {GIN}

func TestFeed(t *testing.T) {{
	r := gin.New()
	ArticlesRegister(r.Group("/test"))
}}
"""
    _write(
        tmp_path,
        {
            "hello.go": MAIN,
            "articles/routers.go": ARTICLES,
            "articles/unit_test.go": test,
            "users/routers.go": USERS,
        },
    )
    paths = {cid for cid, _file, _line in _routes(tmp_path)}
    assert "http::GET::/api/articles/feed" in paths
    assert not any("/test" in p for p in paths)


def test_an_unmounted_function_or_a_method_keeps_its_own_path(tmp_path: Path) -> None:
    main = f"""\
package main

import {GIN}

func main() {{
	r := gin.Default()
	h := NewHandler()
	h.Register(r.Group("/api"))
}}
"""
    handler = f"""\
package main

import {GIN}

func (h *Handler) Register(router *gin.RouterGroup) {{
	router.GET("/items", h.List)
}}

func Orphan(router *gin.RouterGroup) {{
	router.GET("/orphan", Orphan)
}}
"""
    _write(tmp_path, {"main.go": main, "handler.go": handler})
    assert _routes(tmp_path) == [
        ("http::GET::/items", "handler.go", 6),
        ("http::GET::/orphan", "handler.go", 10),
    ]


def test_a_chain_is_read_to_its_top_or_refused() -> None:
    sites = [
        ("a/routes", "Register", 0, "", "/a"),
        ("b/routes", "Register", 0, "api|Setup|0", "/b"),
    ]
    assert served_prefixes(sites, "a/routes", "Register", 0) == ["/a"]
    assert served_prefixes(sites, "a/routes", "Register", 1) is None
    # Setup is never handed a router, so the prefix above /b is unknown.
    assert served_prefixes(sites, "b/routes", "Register", 0) is None
    mounted = [*sites, ("api", "Setup", 0, "", "/api")]
    assert served_prefixes(mounted, "b/routes", "Register", 0) == ["/api/b"]


def test_an_import_is_placed_through_go_mod(tmp_path: Path) -> None:
    # Two packages named `routes`; the go.mod says which one main imports.
    main = f"""package main

import (
	{GIN}
	"example.com/app/api/routes"
)

func main() {{
	r := gin.Default()
	routes.Register(r.Group("/api"))
}}
"""

    def register(path: str) -> str:
        return f"""package routes

import {GIN}

func Register(g *gin.RouterGroup) {{
	g.GET("{path}", H)
}}
"""

    _write(
        tmp_path,
        {
            "go.mod": "module example.com/app\n\ngo 1.22\n",
            "main.go": main,
            "api/routes/r.go": register("/x"),
            "admin/api/routes/r.go": register("/y"),
        },
    )
    assert _routes(tmp_path) == [
        ("http::GET::/api/x", "api/routes/r.go", 6),
        ("http::GET::/y", "admin/api/routes/r.go", 6),
    ]


def _one_file(tmp_path: Path, body: str) -> list[tuple[str, str, int]]:
    _write(tmp_path, {"main.go": f"package main\n\nimport {GIN}\n\n{body}"})
    return [(cid, line) for cid, _file, line in _routes(tmp_path)]


def test_a_name_reused_in_another_function_keeps_each_ones_binding(tmp_path: Path) -> None:
    body = """\
func main() { r := gin.Default(); Reg(r.Group("/a")) }
func Reg(g *gin.RouterGroup) { h := g.Group("/inner"); h.GET("/x", X) }
func Other(r *gin.Engine) { g := r.Group("/o"); g.GET("/y", Y) }
"""
    assert _one_file(tmp_path, body) == [
        ("http::GET::/a/inner/x", 6),
        ("http::GET::/o/y", 7),
    ]


def test_a_function_registering_itself_adds_no_path(tmp_path: Path) -> None:
    body = """\
func main() { r := gin.Default(); Leaf(r.Group("/m")) }
func Leaf(g *gin.RouterGroup) { g.GET("/x", X); Leaf(g.Group("/rec")) }
"""
    assert _one_file(tmp_path, body) == [("http::GET::/m/x", 6)]


def test_a_shadowed_parameter_is_not_the_handed_group(tmp_path: Path) -> None:
    body = """\
func main() { r := gin.Default(); Reg(r.Group("/api")) }
func Reg(g *gin.RouterGroup) { g := gin.New(); g.GET("/x", X) }
"""
    assert _one_file(tmp_path, body) == [("http::GET::/x", 6)]


def test_a_fan_out_past_the_cap_keeps_the_in_function_path(tmp_path: Path) -> None:
    # 2^5 = 32 distinct prefixes reach Leaf; more than the cap is refused whole.
    levels = [
        f'func F{i}(g *gin.RouterGroup) {{ F{i + 1}(g.Group("/p{i}")); F{i + 1}(g.Group("/q{i}")) }}'
        for i in range(5)
    ]
    body = (
        'func main() { r := gin.Default(); F0(r.Group("/api")) }\n'
        + "\n".join(levels)
        + '\nfunc F5(g *gin.RouterGroup) { g.GET("/leaf", X) }\n'
    )
    assert _one_file(tmp_path, body) == [("http::GET::/leaf", 11)]


def test_a_dependency_with_a_matching_directory_is_not_the_repos_package(tmp_path: Path) -> None:
    main = f"""\
package main

import (
	{GIN}
	"github.com/someone/lib/api"
)

func main() {{
	r := gin.Default()
	api.Reg(r.Group("/tp"))
}}
"""
    own = f"""\
package api

import {GIN}

func Reg(g *gin.RouterGroup) {{
	g.GET("/x", X)
}}
"""
    _write(tmp_path, {"go.mod": "module example.com/app\n", "main.go": main, "api/a.go": own})
    assert _routes(tmp_path) == [("http::GET::/x", "api/a.go", 6)]


def test_mutual_registration_adds_no_path(tmp_path: Path) -> None:
    body = """\
func main() { r := gin.Default(); A(r.Group("/m")) }
func A(g *gin.RouterGroup) { g.GET("/a", X); B(g.Group("/b")) }
func B(g *gin.RouterGroup) { g.GET("/bb", X); A(g.Group("/x")) }
"""
    assert _one_file(tmp_path, body) == [("http::GET::/m/a", 6), ("http::GET::/m/b/bb", 7)]


def test_a_methods_groups_stay_in_the_method(tmp_path: Path) -> None:
    body = """\
func main() { r := gin.Default(); Reg(r.Group("/p")) }
func Reg(g *gin.RouterGroup) { g.GET("/c", X) }
func (h *H) Setup(r *gin.Engine) { g := r.Group("/mm"); g.GET("/d", X); Reg(g.Group("/s")) }
"""
    assert _one_file(tmp_path, body) == [
        ("http::GET::/mm/d", 7),
        ("http::GET::/mm/s/c", 6),
        ("http::GET::/p/c", 6),
    ]


def test_a_multi_assigned_parameter_is_rebound(tmp_path: Path) -> None:
    body = """\
func main() { r := gin.Default(); Reg(r.Group("/api")) }
func Reg(g *gin.RouterGroup) { g, err := mk(); g.GET("/x", X) }
"""
    assert _one_file(tmp_path, body) == [("http::GET::/x", 6)]


def test_a_route_closure_handing_on_its_router_adds_its_prefix(tmp_path: Path) -> None:
    body = """\
func main() { r := chi.NewRouter(); r.Route("/api", func(r chi.Router) { Reg(r) }) }
func Reg(r chi.Router) { r.Get("/x", X) }
"""
    _write(tmp_path, {"main.go": f'package main\n\nimport "github.com/go-chi/chi/v5"\n\n{body}'})
    assert [(cid, line) for cid, _f, line in _routes(tmp_path)] == [("http::GET::/api/x", 6)]


def test_a_parameter_hides_a_package_group_of_its_name(tmp_path: Path) -> None:
    body = """\
var r = gin.Default()
var v1 = r.Group("/api")
func main() { F(r.Group("/m")) }
func F(v1 *gin.RouterGroup) { v1.GET("/x", X) }
"""
    assert _one_file(tmp_path, body) == [("http::GET::/m/x", 8)]


def test_a_caller_never_handed_a_router_leaves_the_path_unplaced(tmp_path: Path) -> None:
    body = """\
func Setup(rg *gin.RouterGroup) { Reg(rg.Group("/users")) }
func Reg(g *gin.RouterGroup) { g.GET("/x", X) }
"""
    assert _one_file(tmp_path, body) == [("http::GET::/x", 6)]


def test_a_comma_inside_a_parameter_type_keeps_the_router_position(tmp_path: Path) -> None:
    body = """\
func main() { r := gin.Default(); Reg(nil, r.Group("/api")) }
func Reg(cfg map[string]func(a, b int), g *gin.RouterGroup) { g.GET("/x", X) }
"""
    assert _one_file(tmp_path, body) == [("http::GET::/api/x", 6)]


def test_a_group_from_use_serves_under_the_router_it_was_called_on(tmp_path: Path) -> None:
    """gin's ``Use`` returns the group it is called on: ``authed := router.Use(mw)``."""
    users = f"""\
package users

import {GIN}

func UsersRegister(router *gin.RouterGroup) {{
	authed := router.Use(AuthMiddleware())
	authed.POST("/logout", UsersLogout)
}}
"""
    _write(tmp_path, {"hello.go": MAIN, "articles/routers.go": ARTICLES, "users/routers.go": users})
    assert ("http::POST::/api/users/logout", "users/routers.go", 7) in _routes(tmp_path)


def test_a_group_rebound_from_its_own_use_keeps_its_prefix(tmp_path: Path) -> None:
    main = f"""\
package main

import {GIN}

func main() {{
	r := gin.Default()
	v1 := r.Group("/v1")
	v1 = v1.Use(Auth())
	v1.GET("/ping", Ping)
}}
"""
    _write(tmp_path, {"main.go": main})
    assert _routes(tmp_path) == [("http::GET::/v1/ping", "main.go", 9)]


def test_a_name_two_functions_bind_from_use_takes_neither_parent(tmp_path: Path) -> None:
    main = f"""\
package main

import {GIN}

func a(r *gin.Engine) {{
	api := r.Use(Auth())
	api.GET("/health", Health)
}}

func c(r *gin.Engine) {{
	v1 := r.Group("/v1")
	api := v1.Use(Auth())
	api.GET("/ping", Ping)
}}
"""
    _write(tmp_path, {"main.go": main})
    assert ("http::GET::/health", "main.go", 7) in _routes(tmp_path)
