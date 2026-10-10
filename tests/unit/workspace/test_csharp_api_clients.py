"""C# consumers made through subclasses of a configured typed root client.

The base path is handed to the root constructor by the concrete subclass (through
intermediate bases, from constants in any file), so a bare ``GetAsync<T>(Route)``
resolves to ``/{basePath}/{route}``.
"""

from __future__ import annotations

from pathlib import Path

from repowise.core.workspace.extractors.http import HttpExtractor
from repowise.core.workspace.matching import match_contracts

BASES = ["Contoso.Sdk.RestClient"]
USING = "using Contoso.Sdk;\n"


def _extract(files, bases=BASES, repo="shop"):
    return HttpExtractor(bases).extract(
        Path("."), repo, files=[(p, ".cs", src) for p, src in files]
    )


def _consumers(files, bases=BASES, repo="shop"):
    return [c for c in _extract(files, bases, repo) if c.role == "consumer"]


def _ids(files, bases=BASES):
    return sorted(
        c.contract_id for c in _consumers(files, bases) if c.meta["client"] == "apiclient"
    )


def _client(members: str, base_args: str, extra: str = "") -> list[tuple[str, str]]:
    """A minimal direct subclass of the root, as one file."""
    src = (
        USING
        + f"""
public class Gizmo : RestClient {{
  {extra}
  public Gizmo(HttpClient http) : base({base_args}) {{ }}
  {members}
}}
"""
    )
    return [("Gizmo.cs", src)]


class TestConstructorChainAcrossFiles:
    BASE = (
        "AreaClient.cs",
        USING
        + """
public abstract class AreaClient : RestClient {
  protected AreaClient(string area, HttpClient http) : base(http, area) { }
}
""",
    )
    ALBUMS = (
        "AlbumsApi.cs",
        USING
        + """
public class AlbumsApi : AreaClient {
  const string Area = "albums";
  const string Top = "top";
  public AlbumsApi(HttpClient http) : base(Area, http) { }
  public Task<List<Album>> GetTop() => this.GetAsync<List<Album>>(Top);
}
""",
    )
    FILES = [BASE, ALBUMS]

    def test_constant_endpoint_through_an_intermediate_base(self):
        (c,) = [c for c in _consumers(self.FILES) if c.meta["client"] == "apiclient"]
        assert c.contract_id == "http::GET::/albums/top"
        assert c.meta["base_stripped"] is True
        assert c.file_path == "AlbumsApi.cs"
        assert c.line == 7

    def test_links_to_a_provider_in_another_repo(self):
        provider_src = (
            '[Route("albums")] public class AlbumsController : ControllerBase '
            '{ [HttpGet("top")] public IActionResult Top() => Ok(); }'
        )
        providers = [
            c
            for c in _extract([("A.cs", provider_src)], repo="music-service")
            if c.role == "provider"
        ]
        links = match_contracts(providers + _consumers(self.FILES))
        assert [(k.provider_repo, k.consumer_repo, k.contract_id) for k in links] == [
            ("music-service", "shop", "http::GET::/albums/top")
        ]

    def test_route_constants_declared_in_another_class_and_file(self):
        api = (
            "ForecastApi.cs",
            USING
            + """
public class ForecastApi : RestClient {
  public ForecastApi(IOptions<WeatherOptions> o, HttpClient http) : base(http, "forecast") { }
  public Task<Day> Today() => GetAsync<Day>(ForecastRoutes.Daily);
}
""",
        )
        routes = (
            "Routes.cs",
            'static class ForecastRoutes { public const string Daily = "daily"; }',
        )
        assert _ids([api, routes]) == ["http::GET::/forecast/daily"]


class TestConstructorShapes:
    def test_nested_routes_class_nameof_and_extra_root_arguments(self):
        src = (
            USING
            + """
public class StationsApi : RestClient {
  const string Prefix = nameof(StationsApi);
  public StationsApi(HttpClient http) : base(http, Prefix, TimeSpan.FromSeconds(5)) { }
  public Task<Station> Near(double lat) => this.GetAsync<Station>(Routes.Nearby, new { lat });
  static class Routes { public const string Nearby = nameof(Nearby); }
}
"""
        )
        assert _ids([("S.cs", src)]) == ["http::GET::/stationsapi/nearby"]

    def test_this_chain_in_a_base_class(self):
        base = (
            USING
            + """
public abstract class PlaylistBase : RestClient {
  protected PlaylistBase(HttpClient http, string prefix) : this(prefix, http, 30) { }
  protected PlaylistBase(string prefix, HttpClient http, int seconds) : base(http, prefix) { }
}
"""
        )
        sub = """
public class PlaylistApi : PlaylistBase {
  public PlaylistApi(HttpClient http) : base(http, "playlists") { }
  public Task Add() => PostAsync<int>("add", null);
}
"""
        assert _ids([("B.cs", base), ("P.cs", sub)]) == ["http::POST::/playlists/add"]

    def test_this_chain_in_a_concrete_class(self):
        src = (
            USING
            + """
public class RadioApi : RestClient {
  public RadioApi(HttpClient http) : this(http, "radio") { }
  RadioApi(HttpClient http, string prefix) : base(http, prefix) { }
  Task Live() => GetAsync<int>("stations/live");
}
"""
        )
        assert _ids([("R.cs", src)]) == ["http::GET::/radio/stations/live"]

    def test_this_chain_cycle_terminates_without_a_contract(self):
        src = (
            USING
            + """
public class Loop : RestClient {
  public Loop(HttpClient h, string a) : this(h, a, 1) { }
  public Loop(HttpClient h, string a, int b) : this(h, a) { }
  Task Go() => GetAsync<int>("x/y");
}
"""
        )
        assert _ids([("Loop.cs", src)]) == []

    def test_comments_and_strings_do_not_hide_or_invent_clients(self):
        src = (
            USING
            + """
// class Ghost : RestClient { Ghost() : base(h, "ghost") { } }
public class Real : RestClient {
  const string Url = "http://host/not-a-comment"; /* a stray brace { */
  public Real(HttpClient h) : base(h, "real") { }
  Task A() => GetAsync<int>($"{Url}".Length > 0 ? "a/b" : "c/d");
  Task B() => PostAsync<int>("e/f", null);
}
"""
        )
        assert _ids([("R.cs", src)]) == ["http::POST::/real/e/f"]

    def test_interpolated_route_with_a_method_parameter(self):
        files = _client(
            'Task Rename(int id) => PutAsync<int>($"{Routes.Rename}/{id}", null);',
            'http, "tracks"',
            extra='static class Routes { public const string Rename = "rename"; }',
        )
        assert _ids(files) == ["http::PUT::/tracks/rename/{param}"]

    def test_named_base_path_argument(self):
        files = _client('Task Drop() => DeleteAsync("old/1");', 'http, basePath: "genres"')
        assert _ids(files) == ["http::DELETE::/genres/old/1"]

    def test_rooted_literal_gives_one_prefixed_contract(self):
        files = _client('Task Hot() => this.GetAsync<int>("/hot");', 'http, "albums"')
        assert [(c.contract_id, c.meta["client"]) for c in _consumers(files)] == [
            ("http::GET::/albums/hot", "apiclient")
        ]

    def test_calls_in_a_nested_class_do_not_inherit_the_base_path(self):
        files = _client(
            'Task Own() => GetAsync<int>("own/x");\n'
            '  class Helper { Task Other() => GetAsync<int>("inner/y"); }',
            'http, "albums"',
        )
        assert _ids(files) == ["http::GET::/albums/own/x"]


class TestRootPathArgument:
    ROOT = (
        "RestClient.cs",
        "namespace Contoso.Sdk;\n"
        "public class RestClient {\n"
        "  public RestClient(HttpClient http, string label, string pathPrefix) { }\n"
        "}\n",
    )

    def test_first_constant_string_skips_an_absolute_url(self):
        files = _client(
            'Task Go() => GetAsync<int>("x/y");', 'null, "https://api.example.com", "real"'
        )
        assert _ids(files) == ["http::GET::/real/x/y"]

    def test_first_constant_string_skips_text_with_spaces(self):
        files = _client('Task Go() => GetAsync<int>("x/y");', 'http, "Sample client", "real"')
        assert _ids(files) == ["http::GET::/real/x/y"]

    def test_a_path_named_root_parameter_wins_over_an_earlier_string(self):
        files = _client('Task Go() => GetAsync<int>("x/y");', 'http, "weather", "forecast"')
        assert _ids(files) == ["http::GET::/weather/x/y"]
        assert _ids([*files, self.ROOT]) == ["http::GET::/forecast/x/y"]

    def test_an_unresolved_path_named_argument_is_not_replaced_by_another_string(self):
        files = _client('Task Go() => GetAsync<int>("x/y");', 'http, "weather", prefix')
        assert _ids([*files, self.ROOT]) == []


class TestNotRecognised:
    def test_root_name_from_a_different_namespace(self):
        src = (
            "using Fabrikam.Sdk;\n"
            'public class Gadget : RestClient { public Gadget(HttpClient h) : base(h, "gadget") { }'
            ' Task Go() => GetAsync<int>("path/x"); }'
        )
        anchor = ("Anchor.cs", "// mentions Contoso.Sdk only in a comment")
        assert _ids([anchor, ("Gadget.cs", src)]) == []

    def test_path_supplied_by_dependency_injection(self):
        src = (
            USING
            + """
public class Di : RestClient {
  public Di(HttpClient h, string basePath) : base(h, basePath) { }
  Task Go() => GetAsync<int>("path/x");
}
"""
        )
        assert _ids([("Di.cs", src)]) == []

    def test_root_called_without_a_resolvable_path(self):
        assert _ids(_client('Task Go() => GetAsync<int>("x/y");', "http")) == []

    def test_constant_declared_twice_with_different_values(self):
        client = _client("Task Go() => GetAsync<int>(Eps.Get);", 'http, "base"')
        eps = [
            ("A.cs", 'static class Eps { public const string Get = "one"; }'),
            ("B.cs", 'static class Eps { public const string Get = "two"; }'),
        ]
        assert _ids(client + eps) == []

    def test_constructors_resolving_to_different_paths(self):
        src = (
            USING
            + """
public class Two : RestClient {
  public Two(HttpClient h) : base(h, "a") { }
  public Two(HttpClient h, int x) : base(h, "b") { }
  Task Go() => GetAsync<int>("x/y");
}
"""
        )
        assert _ids([("Two.cs", src)]) == []

    def test_duplicate_class_name(self):
        one = _client('Task Go() => GetAsync<int>("x/y");', 'http, "a"')[0]
        assert _ids([one, ("Other.cs", one[1])]) == []

    def test_inheritance_cycle_terminates(self):
        a = USING + 'class A : B { A(int x) : base(x) { } Task Go() => GetAsync<int>("x/y"); }'
        b = "class B : A { B(int x) : base(x) { } }"
        assert _ids([("A.cs", a), ("B.cs", b)]) == []

    def test_primary_constructor_is_not_resolved(self):
        src = USING + (
            'public class P(HttpClient h) : RestClient(h, "p") '
            '{ Task G() => GetAsync<int>("x/y"); }'
        )
        assert _ids([("P.cs", src)]) == []


class TestUnchangedHttpClientBehaviour:
    def test_receiver_call_inside_a_subclass_stays_with_the_plain_recogniser(self):
        files = _client('Task Go() => _http.GetAsync("/x/y");', 'http, "base"')
        (c,) = _consumers(files)
        assert (c.contract_id, c.meta["client"]) == ("http::GET::/x/y", "httpclient")
        assert "base_stripped" not in c.meta

    def test_plain_httpclient_outside_any_client_subclass(self):
        src = (
            "class S { void Run(HttpClient client) "
            '{ client.GetAsync("https://api.example.com/foo"); } }'
        )
        (c,) = _consumers([("S.cs", src)])
        assert (c.contract_id, c.meta["client"]) == ("http::GET::/foo", "httpclient")


class TestConfiguredRoots:
    CATALOG = (
        "Catalog.cs",
        "using Contoso.Sdk;\n"
        "public class CatalogClient : RestClient {\n"
        '  public CatalogClient(IOptions<CatalogOptions> o) : base(o, "catalog") { }\n'
        '  Task Go() => GetAsync<int>("list");\n'
        "}\n",
    )
    WEATHER = (
        "Weather.cs",
        "using Fabrikam.Sdk;\n"
        "public class WeatherClient : Gateway {\n"
        '  public WeatherClient(IOptions<WeatherOptions> o) : base(o, "weather") { }\n'
        '  Task Go() => GetAsync<int>("open");\n'
        "}\n",
    )

    def test_no_base_configured_reads_no_typed_clients(self):
        assert _ids([self.CATALOG], []) == []

    def test_several_roots_are_each_resolved(self):
        bases = ["Contoso.Sdk.RestClient", "Fabrikam.Sdk.Gateway"]
        assert _ids([self.CATALOG, self.WEATHER], bases) == [
            "http::GET::/catalog/list",
            "http::GET::/weather/open",
        ]

    def test_a_root_not_named_is_not_read(self):
        assert _ids([self.CATALOG, self.WEATHER], BASES) == ["http::GET::/catalog/list"]

    def test_a_bare_root_name_matches_by_class_name(self):
        assert _ids([self.WEATHER], ["Gateway"]) == ["http::GET::/weather/open"]
