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
from repowise.core.workspace.extractors.http.go import served_prefixes

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


def test_the_package_is_placed_by_its_module_else_by_its_tail() -> None:
    sites = [
        ("=a/routes", "Register", 0, "", "/a"),
        ("=routes", "Register", 0, "", "/root"),
        ("~example.com/other/b/routes", "Register", 0, "", "/b"),
    ]
    assert served_prefixes(sites, "a/routes", "Register", 0) == ["/a"]
    # An import path no go.mod places is taken for any directory it ends in.
    assert served_prefixes(sites, "routes", "Register", 0) == ["/b", "/root"]
    assert served_prefixes(sites, "b/routes", "Register", 0) == ["/b"]
    assert served_prefixes(sites, "a/routes", "Register", 1) is None


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
