"""Unit tests for ASP.NET Web API 2 and ASP.NET Core HTTP contract extraction."""

from __future__ import annotations

from repowise.core.workspace.extractors.base import ScanContext
from repowise.core.workspace.extractors.http.aspnet import AspNetDialect


def _extract(content: str, rel_path: str = "Controllers/OrdersController.cs") -> dict[str, str]:
    ctx = ScanContext(
        repo_alias="backend",
        rel_path=rel_path,
        suffix=".cs",
        content=content,
    )
    contracts = AspNetDialect().extract(ctx)
    return {c.contract_id: c.symbol_name for c in contracts}


def test_web_api_2_routeprefix_with_route_below_httpget():
    """Classic ASP.NET Web API 2: [RoutePrefix] on class and [Route] below [HttpGet]."""
    content = """\
[RoutePrefix("Orders")]
public class OrdersController : ApiController
{
    [HttpGet]
    [Route("Open")]
    public IHttpActionResult Open() { return Ok(); }

    [HttpGet]
    [Route("Closed")]
    public IHttpActionResult Closed() { return Ok(); }

    [HttpGet]
    [Route("Archived")]
    public IHttpActionResult Archived() { return Ok(); }
}
"""
    contracts = _extract(content)
    assert "http::GET::/orders/open" in contracts
    assert "http::GET::/orders/closed" in contracts
    assert "http::GET::/orders/archived" in contracts
    assert len(contracts) == 3


def test_route_above_http_verb():
    """Action-level [Route] placed above [HttpGet]."""
    content = """\
[RoutePrefix("api/v1/orders")]
public class OrdersController : ApiController
{
    [Route("pending")]
    [HttpGet]
    public IHttpActionResult Pending() { return Ok(); }
}
"""
    contracts = _extract(content)
    assert "http::GET::/api/v1/orders/pending" in contracts


def test_combined_attribute_lists():
    """Combined [HttpGet, Route("...")] and [Route("..."), HttpPost] attribute lists."""
    content = """\
[RoutePrefix("api")]
public class ApiController : Controller
{
    [HttpGet, Route("items")]
    public IActionResult GetItems() { return Ok(); }

    [Route("details"), HttpPost]
    public IActionResult PostDetails() { return Ok(); }
}
"""
    contracts = _extract(content)
    assert "http::GET::/api/items" in contracts
    assert "http::POST::/api/details" in contracts


def test_tilde_prefix_override():
    """A route starting with ~ overrides and ignores the class RoutePrefix."""
    content = """\
[RoutePrefix("api/v1")]
public class StatusController : ApiController
{
    [HttpGet]
    [Route("~/healthz")]
    public IHttpActionResult Health() { return Ok(); }
}
"""
    contracts = _extract(content)
    assert "http::GET::/healthz" in contracts
    assert len(contracts) == 1


def test_bare_verb_falls_back_to_class_prefix():
    """Parameterless [HttpPost] inherits the class route prefix."""
    content = """\
[RoutePrefix("api/users")]
public class UsersController : ApiController
{
    [HttpPost]
    public IHttpActionResult CreateUser() { return Ok(); }
}
"""
    contracts = _extract(content)
    assert "http::POST::/api/users" in contracts


def test_aspnetcore_inline_route():
    """ASP.NET Core [HttpGet("...")] attribute routing."""
    content = """\
[Route("api/[controller]")]
[ApiController]
public class UsersController : ControllerBase
{
    [HttpGet("{id}")]
    public IActionResult GetUser(string id) { return Ok(); }
}
"""
    contracts = _extract(content)
    assert "http::GET::/api/{param}/{param}" in contracts
    assert contracts["http::GET::/api/{param}/{param}"] == "aspnet:GET api/[controller]/{id}"


def test_unrouted_controller_bare_verb_dropped():
    """A bare [HttpPost] without any class or action route is dropped."""
    content = """\
public class PlainController : ApiController
{
    [HttpPost]
    public IHttpActionResult Create() { return Ok(); }
}
"""
    contracts = _extract(content)
    assert len(contracts) == 0


def test_attribute_block_with_comments():
    """Comments interspersed between attribute lists are handled cleanly."""
    content = """\
[RoutePrefix("api/v2")]
public class CommentedController : ApiController
{
    [HttpGet]
    // Route for items
    [Route("items")]
    public IHttpActionResult Items() { return Ok(); }
}
"""
    contracts = _extract(content)
    assert "http::GET::/api/v2/items" in contracts


def test_verb_in_comment_and_string_ignored():
    """HttpGet inside comments or string literals must not anchor to array indices."""
    content = """\
[RoutePrefix("api/v3")]
public class NonAttributeController : ApiController
{
    // HttpGet should not be treated as an action attribute
    // [HttpGet] commented-out attribute
    public IHttpActionResult ReadItems()
    {
        var items = new int[] { 10, 20, 30 };
        string desc = "Performs HttpGet request";
        string tag = "[HttpGet]";
        return Ok(items[0]);
    }

    [HttpGet]
    [Route("real")]
    public IHttpActionResult RealAction()
    {
        return Ok();
    }
}
"""
    contracts = _extract(content)
    assert "http::GET::/api/v3/real" in contracts
    assert len(contracts) == 1


def test_class_route_template_with_bracket_token_and_custom_attributes():
    """Class route template [Route("api/[controller]")] preserves prefix even with bracketed attributes and comments."""
    content = """\
[ApiController]
[Route("api/[controller]")]
// Some controller documentation
[Custom("[tag]")]
public class UsersController : ControllerBase
{
    [HttpGet]
    public IActionResult GetAll() { return Ok(); }
}
"""
    contracts = _extract(content)
    assert "http::GET::/api/{param}" in contracts
    assert contracts["http::GET::/api/{param}"] == "aspnet:GET api/[controller]"


def test_action_route_template_with_bracket_token():
    """Action route containing bracket token like [id] is not truncated or dropped."""
    content = """\
[RoutePrefix("api")]
public class ItemsController : ApiController
{
    [Route("items/[id]")]
    [HttpGet]
    public IHttpActionResult GetById(int id) { return Ok(); }

    [HttpPost, Route("items/[id]/update")]
    public IHttpActionResult Update(int id) { return Ok(); }
}
"""
    contracts = _extract(content)
    assert "http::GET::/api/items/{param}" in contracts
    assert "http::POST::/api/items/{param}/update" in contracts
    assert len(contracts) == 2


def test_inline_url_on_prior_line_does_not_hang():
    """An inline URL on a prior line (e.g. var u = "http://host";) does not cause infinite scan."""
    content = """\
[RoutePrefix("api")]
public class HostController : ApiController
{
    public void Init()
    {
        var u = "http://host";
    }

    [HttpGet]
    [Route("ping")]
    public IHttpActionResult Ping() { return Ok(); }
}
"""
    contracts = _extract(content)
    assert "http::GET::/api/ping" in contracts
    assert len(contracts) == 1

