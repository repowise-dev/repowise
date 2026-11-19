"""Go routers beyond gin and echo: chi, fiber, gorilla/mux and Go 1.22 patterns.

Each produced no provider, or a wrong one, before: chi and fiber spell verbs in
title case, gorilla builds a route as a chain of matchers, and a Go 1.22
``"GET /x"`` pattern was stored as the path ``/get /x``. The snippets are cut
from Sock Shop's catalogue and payment services and Online Boutique's frontend.
"""

from __future__ import annotations

from repowise.core.ingestion.framework_routes import go_routes
from repowise.core.ingestion.framework_routes_chi import route_scopes, title_case_routes
from repowise.core.ingestion.framework_routes_gorilla import gorilla_groups, gorilla_routes
from repowise.core.workspace.extractors.base import ScanContext
from repowise.core.workspace.extractors.dialect import run_dialect
from repowise.core.workspace.extractors.http.go import GoDialect
from repowise.core.workspace.origins import ContractOrigin


def _provided(src: str, name: str = "routes.go") -> list[tuple[str, int, str | None]]:
    contracts = run_dialect(GoDialect(), ScanContext("svc", name, ".go", src))
    return sorted((c.contract_id, c.line, c.meta.get("handler")) for c in contracts)


# Sock Shop catalogue/transport.go and payment/transport.go (go-kit on gorilla/mux).
SOCKSHOP_TRANSPORT = """\
package catalogue

import (
	"github.com/gorilla/mux"
	httptransport "github.com/go-kit/kit/transport/http"
)

func MakeHTTPHandler(e Endpoints, imagePath string) *mux.Router {
	r := mux.NewRouter().StrictSlash(false)
	// GET /catalogue       List
	r.Methods("GET").Path("/catalogue").Handler(httptransport.NewServer(
		e.ListEndpoint,
		decodeListRequest,
	))
	r.Methods("GET").Path("/catalogue/{id}").Handler(httptransport.NewServer(
		e.GetEndpoint, decodeGetRequest,
	))
	r.Methods("POST").Path("/paymentAuth").Handler(authHandler)
	r.Methods("GET").PathPrefix("/health").Handler(healthHandler)
	r.Handle("/metrics", promhttp.Handler())
	return r
}
"""

# Online Boutique frontend/main.go: a prefix variable the deployment sets.
BOUTIQUE_MAIN = """\
package main

import "github.com/gorilla/mux"

var (
	baseUrl = ""
)

func main() {
	baseUrl = os.Getenv("BASE_URL")
	r := mux.NewRouter()
	r.HandleFunc(baseUrl+"/product/{id}", svc.productHandler).Methods(http.MethodGet, http.MethodHead)
	r.HandleFunc(baseUrl+"/cart", svc.addToCartHandler).Methods(http.MethodPost)
	r.HandleFunc(baseUrl+"/_healthz", func(w http.ResponseWriter, _ *http.Request) { fmt.Fprint(w, "ok") })
}
"""

CHI_ROUTES = """\
package api

import "github.com/go-chi/chi/v5"

type Server struct {
	cache *Cache
}

func (s *Server) Routes() http.Handler {
	r := chi.NewRouter()
	r.Get("/ping", s.ping)
	r.Route("/articles", func(r chi.Router) {
		r.Post("/", s.createArticle)
		r.Route("/{articleID}", func(r chi.Router) {
			r.Get("/", s.getArticle)
			r.Delete("/comments/{id}", s.deleteComment)
		})
	})
	s.cache.Get("/not/a/route")
	return r
}
"""

FIBER_APP = """\
package main

import "github.com/gofiber/fiber/v2"

func main() {
	app := fiber.New()
	api := app.Group("/api")
	api.Get("/users/:id", getUser)
	app.Route("/v2", func(v2 fiber.Router) {
		v2.Put("/orders/:id", updateOrder)
	})
	app.Post("/login", func(c *fiber.Ctx) error {
		token := c.Get("Authorization")
		return nil
	})
}
"""


class TestGorilla:
    def test_sock_shop_chains_serve_their_paths_and_verbs(self) -> None:
        assert _provided(SOCKSHOP_TRANSPORT) == [
            ("http::*::/metrics", 20, None),
            ("http::GET::/catalogue", 11, None),
            ("http::GET::/catalogue/{param}", 15, None),
            ("http::GET::/health", 19, "healthHandler"),
            ("http::POST::/paymentauth", 18, "authHandler"),
        ]

    def test_a_commented_out_chain_is_not_a_route(self) -> None:
        src = SOCKSHOP_TRANSPORT.replace(
            'r.Methods("POST")', '// r.Methods("POST")'
        )
        assert "http::POST::/paymentauth" not in {c for c, _, _ in _provided(src)}

    def test_a_prefix_variable_the_file_does_not_settle_is_the_deployment_base(self) -> None:
        # `baseUrl` is reassigned from the environment, so it is dropped as a
        # consumer's leading base is; HEAD is not a recorded method.
        assert _provided(BOUTIQUE_MAIN, "main.go") == [
            ("http::*::/_healthz", 14, None),
            ("http::GET::/product/{param}", 12, "svc.productHandler"),
            ("http::POST::/cart", 13, "svc.addToCartHandler"),
        ]

    def test_a_constant_prefix_is_inlined(self) -> None:
        src = (
            'import "github.com/gorilla/mux"\n'
            'const apiBase = "/api/v1"\n'
            'r.HandleFunc(apiBase+"/orders", h).Methods("POST")\n'
        )
        assert _provided(src) == [("http::POST::/api/v1/orders", 3, "h")]

    def test_a_partly_read_path_is_not_a_route(self) -> None:
        # A literal with more appended is only the path's head, and a
        # one-argument call on a logger is not a registration.
        src = (
            'r.GET("/api"+ver+"/x", h)\n'
            'r.GET(base+"/x"+suffix, h)\n'
            'log.Handle(prefix+"/x")\n'
        )
        assert [(r.verb, r.path) for r in go_routes(src)] == []

    def test_subrouters_compose_and_an_unreadable_prefix_refuses(self) -> None:
        src = (
            'import "github.com/gorilla/mux"\n'
            'api := r.PathPrefix("/api").Subrouter()\n'
            'v1 := api.PathPrefix("/v1").Subrouter()\n'
            'v1.HandleFunc("/items", items).Methods("GET")\n'
            'dyn := r.PathPrefix(prefix()).Subrouter()\n'
            'dyn.HandleFunc("/hidden", hidden)\n'
            'below := dyn.PathPrefix("/x").Subrouter()\n'
            'below.HandleFunc("/deeper", deeper)\n'
        )
        assert [(g.var, g.parent, g.prefix) for g in gorilla_groups(src)] == [
            ("api", "r", "/api"),
            ("v1", "api", "/v1"),
            ("dyn", "r", None),
            ("below", "dyn", "/x"),
        ]
        assert _provided(src) == [("http::GET::/api/v1/items", 4, "items")]

    def test_an_unreadable_methods_list_leaves_the_route_verbless(self) -> None:
        src = 'import "github.com/gorilla/mux"\nr.Path("/x").Methods(verbs...).HandlerFunc(h)\n'
        assert _provided(src) == [("http::*::/x", 2, "h")]

    def test_matchers_after_the_handler_are_read(self) -> None:
        # Handler returns the *Route, so gorilla accepts matchers after it.
        src = 'import "github.com/gorilla/mux"\nr.Path("/x").Handler(h).Methods("GET")\n'
        assert _provided(src) == [("http::GET::/x", 2, "h")]

    def test_chains_are_read_only_in_a_gorilla_file(self) -> None:
        src = 'r.Methods("GET").Path("/x").Handler(h)\n'
        assert list(gorilla_routes(src)) == []

    def test_a_builder_with_no_handler_is_not_a_route(self) -> None:
        src = 'import "github.com/gorilla/mux"\nroute := r.Path("/x").Methods("GET")\n'
        assert list(gorilla_routes(src)) == []


class TestChiAndFiber:
    def test_route_closures_nest_and_only_router_receivers_count(self) -> None:
        # The root `/` of a closure is kept as its prefix; `s.cache.Get` is not
        # a router however its argument looks.
        assert _provided(CHI_ROUTES) == [
            ("http::DELETE::/articles/{param}/comments/{param}", 16, "s.deleteComment"),
            ("http::GET::/articles/{param}", 15, "s.getArticle"),
            ("http::GET::/ping", 11, "s.ping"),
            ("http::POST::/articles", 13, "s.createArticle"),
        ]
        assert [(s.outer, s.inner, s.prefix) for s in route_scopes(CHI_ROUTES)] == [
            ("r", "r", "/articles"),
            ("r", "r", "/{articleID}"),
        ]

    def test_fiber_groups_routes_and_a_header_read(self) -> None:
        assert _provided(FIBER_APP) == [
            ("http::GET::/api/users/{param}", 8, "getUser"),
            ("http::POST::/login", 12, None),
            ("http::PUT::/v2/orders/{param}", 10, "updateOrder"),
        ]

    def test_title_case_verbs_need_the_router_import(self) -> None:
        src = CHI_ROUTES.replace('import "github.com/go-chi/chi/v5"', 'import "example.com/kv"')
        assert list(title_case_routes(src)) == []

    def test_a_receiver_is_read_at_its_nearest_binding(self) -> None:
        # The handler literal's `r *http.Request` holds only inside its body, so
        # the next route on the router `r` still counts; a later function that
        # takes a client named `r`, or groups a non-router, adds no route.
        src = (
            'import "github.com/go-chi/chi/v5"\n'
            "func routes() {\n"
            "\tr := chi.NewRouter()\n"
            '\tr.Get("/a", func(w http.ResponseWriter, r *http.Request) { r.Context() })\n'
            '\tr.Post("/b", b)\n'
            "}\n"
            "func fetch(r *Client) {\n"
            '\tr.Get("/users/1", nil)\n'
            '\tg := r.Group("/x")\n'
            '\tg.Get("/y", nil)\n'
            "}\n"
        )
        assert [(r.verb, r.path) for r in title_case_routes(src)] == [
            ("GET", "/a"),
            ("POST", "/b"),
        ]

    def test_a_client_call_is_not_a_route(self) -> None:
        # An HTTP client in a chi service: neither receiver is bound to a router.
        src = (
            'import "github.com/go-chi/chi/v5"\n'
            'resp, err := client.Get("/users")\n'
            'resp, err = http.Get("/users")\n'
        )
        assert list(title_case_routes(src)) == []


class TestGo122Patterns:
    def test_a_method_pattern_names_the_verb(self) -> None:
        src = (
            'mux.HandleFunc("GET /items/{id}", getItem)\n'
            'mux.HandleFunc("POST api.example.com/items", createItem)\n'
            'mux.HandleFunc("/files/{path...}", files)\n'
            'mux.HandleFunc("GET /{$}", home)\n'
        )
        assert [(r.verb, r.path) for r in go_routes(src)] == [
            ("GET", "/items/{id}"),
            ("POST", "/items"),
            ("HANDLEFUNC", "/files/{path}"),
            ("GET", "/"),
        ]
        assert _provided(src) == [
            ("http::*::/files/{param}", 3, "files"),
            ("http::GET::/", 4, "home"),
            ("http::GET::/items/{param}", 1, "getItem"),
            ("http::POST::/items", 2, "createItem"),
        ]

    def test_origin_is_route_registration(self) -> None:
        (contract,) = run_dialect(
            GoDialect(),
            ScanContext("svc", "m.go", ".go", 'mux.HandleFunc("GET /a", h)\n'),
        )
        assert contract.origin == ContractOrigin.ROUTE_REGISTRATION
