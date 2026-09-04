"""Ruby HTTP client calls become consumer contracts.

A Rails service names its endpoint as a frozen constant and interpolates the
rest (``"#{BASE_URL}/accounts/#{id}"``), writes the call across several lines,
and sometimes drops the parentheses altogether. The claims under test are that
each of those reaches the same contract a quoted literal would, that a
connection variable's ``get`` counts only when the file bound it with
``Faraday.new``, and that a URL the file does not settle -- a helper method, a
parameter, the ``uri`` a ``Net::HTTP.start`` block receives -- emits nothing.
"""

from __future__ import annotations

from pathlib import Path

from repowise.core.workspace.contracts import match_contracts
from repowise.core.workspace.extractors.base import ScanContext
from repowise.core.workspace.extractors.http import HttpExtractor
from repowise.core.workspace.extractors.http.fastapi import FastApiDialect
from repowise.core.workspace.extractors.http.ruby_clients import (
    RubyClientsDialect,
    faraday_connection_calls,
    module_calls,
    net_http_calls,
)

HTTPARTY_RB = """\
class Dyte
  BASE_URL = 'https://api.cloudflare.com/client/v4'.freeze

  def post(payload)
    HTTParty.post(
      "#{BASE_URL}/accounts/realtime/kit", {
        body: payload&.to_json
      }.compact
    )
  end

  def show
    HTTParty.get("#{BASE_URL}/accounts/#{@account_id}/meetings")
  end
end
"""

RESTCLIENT_RB = """\
class Hub
  ENDPOINT = 'https://hub.example.com/ping'.freeze

  def self.sync
    RestClient.post ENDPOINT, info.to_json, { content_type: :json }
  end

  def self.drop(id)
    RestClient.delete("https://hub.example.com/instances/" + id)
  end
end
"""

FARADAY_RB = """\
module KeyValidator
  def self.valid?
    connection = Faraday.new do |f|
      f.options.timeout = 5
    end
    connection.get('https://api.openai.com/v1/models')
  end
end
"""

FARADAY_UNBOUND_RB = """\
class Client
  def initialize(conn)
    @conn = conn
  end

  def show(name)
    @conn.get("/repos/#{name}")
  end
end
"""

NET_HTTP_RB = """\
require 'net/http'

class Probe
  HEALTH = 'https://search.example.com/_cluster/health'.freeze

  def self.check
    Net::HTTP.get_response(URI(HEALTH))
  end

  def self.report
    Net::HTTP.post(URI.parse('https://search.example.com/reports'), body)
  end
end
"""

NET_HTTP_OPAQUE_RB = """\
require 'net/http'

class Fetcher
  def fetch_once(uri, ip_address)
    request = build_request(uri)
    Net::HTTP.start(uri.hostname, uri.port) do |http|
      http.request(Net::HTTP::Get.new(uri))
    end
  end
end
"""

HELPER_URL_RB = """\
class Hub
  def self.base_url
    'https://hub.example.com'
  end

  def self.ping_url
    "#{base_url}/ping"
  end

  def self.sync
    RestClient.post(ping_url, info.to_json)
  end

  def self.crawl(external_link)
    HTTParty.get(external_link)
  end
end
"""

LINK_CLIENT_RB = """\
class Meetings
  def show(account_id)
    HTTParty.get("#{api_base}/accounts/#{account_id}/meetings")
  end
end
"""

PROVIDER_PY = """\
from fastapi import FastAPI

app = FastAPI()


@app.get("/accounts/{account_id}/meetings")
def meetings(account_id: str):
    return []
"""


def _contracts(content: str, rel_path: str = "lib/client.rb", alias: str = "web") -> list:
    ctx = ScanContext(alias, rel_path, ".rb", content, {}, None)
    return RubyClientsDialect().extract(ctx)


class TestRubyClientsRecognition:
    def test_multi_line_and_single_line_module_calls(self) -> None:
        rows = list(module_calls(HTTPARTY_RB))
        assert [(r.client, r.method) for r in rows] == [
            ("httparty", "POST"),
            ("httparty", "GET"),
        ]
        assert rows[0].url == '"#{BASE_URL}/accounts/realtime/kit"'

    def test_parentheses_may_be_omitted(self) -> None:
        rows = list(module_calls(RESTCLIENT_RB))
        assert [(r.client, r.method, r.url) for r in rows] == [
            ("restclient", "POST", "ENDPOINT"),
            ("restclient", "DELETE", '"https://hub.example.com/instances/" + id'),
        ]

    def test_net_http_verb_comes_from_the_callee(self) -> None:
        rows = list(net_http_calls(NET_HTTP_RB))
        # The URI wrapper is peeled by the shared resolver, so the row keeps
        # the argument as written.
        assert [(r.method, r.url) for r in rows] == [
            ("GET", "URI(HEALTH)"),
            ("POST", "URI.parse('https://search.example.com/reports')"),
        ]

    def test_request_object_names_the_verb_by_class(self) -> None:
        rows = list(net_http_calls(NET_HTTP_OPAQUE_RB))
        assert [(r.method, r.url) for r in rows] == [("GET", "uri")]

    def test_connection_receiver_comes_from_faraday_new(self) -> None:
        rows = list(faraday_connection_calls(FARADAY_RB))
        assert [(r.method, r.confidence) for r in rows] == [("GET", 0.65)]


class TestRubyClientsContracts:
    def test_constant_folds_into_an_interpolated_url(self) -> None:
        ids = {c.contract_id for c in _contracts(HTTPARTY_RB)}
        assert "http::POST::/client/v4/accounts/realtime/kit" in ids
        assert "http::GET::/client/v4/accounts/{param}/meetings" in ids

    def test_client_label_and_host_are_recorded(self) -> None:
        by_id = {c.contract_id: c for c in _contracts(HTTPARTY_RB)}
        c = by_id["http::POST::/client/v4/accounts/realtime/kit"]
        assert c.meta["client"] == "httparty"
        assert c.meta["host"] == "api.cloudflare.com"
        assert c.role == "consumer"
        assert c.confidence == 0.75

    def test_concatenation_and_a_bare_constant_both_resolve(self) -> None:
        ids = {c.contract_id for c in _contracts(RESTCLIENT_RB)}
        assert ids == {"http::POST::/ping", "http::DELETE::/instances/{param}"}

    def test_restclient_rows_name_their_library(self) -> None:
        assert {c.meta["client"] for c in _contracts(RESTCLIENT_RB)} == {"restclient"}

    def test_uri_wrapper_is_peeled(self) -> None:
        by_id = {c.contract_id: c for c in _contracts(NET_HTTP_RB)}
        assert set(by_id) == {"http::GET::/_cluster/health", "http::POST::/reports"}
        assert by_id["http::GET::/_cluster/health"].meta["client"] == "net-http"

    def test_faraday_connection_call_is_lower_confidence(self) -> None:
        contracts = _contracts(FARADAY_RB)
        assert [(c.contract_id, c.confidence) for c in contracts] == [
            ("http::GET::/v1/models", 0.65)
        ]
        assert contracts[0].meta["client"] == "faraday"

    def test_base_expression_is_stripped_for_the_matcher(self) -> None:
        content = 'HTTParty.get("#{api_base_path}/v1/Content/#{content_sid}")\n'
        (c,) = _contracts(content)
        assert c.contract_id == "http::GET::/v1/content/{param}"
        assert c.meta["base_stripped"] is True
        assert c.meta["base_token"] == "api_base_path"


class TestRubyClientsRefusals:
    def test_helper_method_url_emits_nothing(self) -> None:
        assert _contracts(HELPER_URL_RB) == []

    def test_opaque_uri_in_a_start_block_emits_nothing(self) -> None:
        assert _contracts(NET_HTTP_OPAQUE_RB) == []

    def test_connection_not_bound_by_faraday_new_emits_nothing(self) -> None:
        assert _contracts(FARADAY_UNBOUND_RB) == []

    def test_settings_reader_is_not_an_options_request(self) -> None:
        content = "conn = Faraday.new\nconn.options.timeout = 5\n"
        assert list(faraday_connection_calls(content)) == []

    def test_a_url_expression_ending_in_a_call_emits_nothing(self) -> None:
        content = "HTTParty.get(inbox.channel.media_url(attachment_payload[:id]))\n"
        assert _contracts(content) == []

    def test_name_assigned_twice_does_not_fold(self) -> None:
        content = (
            "path = 'meetings'\n"
            "path = 'participants'\n"
            'HTTParty.get("https://api.example.com/accounts/#{path}")\n'
        )
        (c,) = _contracts(content)
        assert c.contract_id == "http::GET::/accounts/{param}"


class TestRubyClientsLink:
    def test_consumer_links_to_a_fastapi_provider(self, tmp_path: Path) -> None:
        api = tmp_path / "api"
        api.mkdir()
        (api / "main.py").write_text(PROVIDER_PY, encoding="utf-8")
        web = tmp_path / "web"
        web.mkdir()
        (web / "client.rb").write_text(LINK_CLIENT_RB, encoding="utf-8")

        # `source_extensions` reads the class, so both sides are named on a
        # subclass rather than an instance: the test then depends on this pair
        # of dialects only, not on whatever the default tuples carry.
        class RubyExtractor(HttpExtractor):
            provider_dialects = (FastApiDialect(),)
            consumer_dialects = (RubyClientsDialect(),)

        extractor = RubyExtractor()
        providers = [c for c in extractor.extract(api, "api") if c.role == "provider"]
        consumers = [c for c in extractor.extract(web, "web") if c.role == "consumer"]

        links = match_contracts(providers + consumers)
        assert [(link.provider_repo, link.consumer_repo) for link in links] == [("api", "web")]
