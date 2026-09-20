"""The PHP HTTP consumer dialect: Guzzle and the Laravel ``Http`` facade.

The claim under test is that a PHP call site is read only when the file names
an HTTP client and the URL argument settles inside the file: a literal, an
interpolated string, or a concatenation onto a folded constant. A property, a
``config(...)`` lookup and a runtime verb are refused rather than guessed.
"""

from __future__ import annotations

from pathlib import Path

from repowise.core.workspace.contracts import match_contracts
from repowise.core.workspace.extractors.base import ScanContext
from repowise.core.workspace.extractors.http import HttpExtractor
from repowise.core.workspace.extractors.http.fastapi import FastApiDialect
from repowise.core.workspace.extractors.http.php_clients import (
    PhpClientsDialect,
    guzzle_request_calls,
    guzzle_verb_calls,
    laravel_http_calls,
)

GUZZLE_REQUEST = """<?php

use GuzzleHttp\\Client;

class Sync
{
    public function run(Client $client, string $id): void
    {
        $client->request('GET', '/api/accounts');
        $client->request('POST', "https://svc.example.com/api/accounts/{$id}/sync");
    }
}
"""

GUZZLE_VERBS = """<?php

use GuzzleHttp\\Client;

class Fetcher
{
    public function run(): void
    {
        $client = new Client();
        $client->get('http://billing/api/invoices');
        $this->client->delete('/api/invoices/1');
    }
}
"""

GUZZLE_CONCAT = """<?php

class Reports
{
    private ClientInterface $client;

    public function run(): void
    {
        $base = 'https://reports.internal';
        $this->client->get($base . '/api/reports');
    }
}
"""

GUZZLE_PROPERTY_BASE = """<?php

use GuzzleHttp\\Client;

class Contacts
{
    public function run(Client $client): void
    {
        $client->request('GET', $this->base . '/contacts');
    }
}
"""

GUZZLE_BASE_RELATIVE = """<?php

use GuzzleHttp\\Client;

class Mixed
{
    public function run(Client $client): void
    {
        $client->request('GET', 'some/path');
        $client->request('GET', '/some/path');
        $client->request('GET', 'https://h/x');
    }
}
"""

LARAVEL_HTTP = """<?php

use Illuminate\\Support\\Facades\\Http;

class Wikipedia
{
    public function run(string $lang, string $id): void
    {
        Http::get("https://$lang.example.org/w/api.php?format=json");
        Http::withToken('t')->post('https://svc.example.com/api/jobs');
        Http::withHeaders(['X-A' => 'b'])->get("/api/items/{$id}");
    }
}
"""

UNGATED = """<?php

class Repository
{
    public function run(): void
    {
        $cache->get('/config/settings');
        $store->request('GET', '/config/settings');
    }
}
"""

REFUSALS = """<?php

use GuzzleHttp\\Client;
use Illuminate\\Support\\Facades\\Http;

class Dav
{
    public function run(Client $client, string $verb): void
    {
        $client->request('GET', $this->uri);
        $client->request($verb, '/api/things');
        $client->get($url);
        $client->get(config('services.things.url') . '/api/things');
        Http::get($this->endpoint);
        Http::fake();
        Http::get('example.com');
    }
}
"""


def _ctx(content: str, rel_path: str = "app/Client.php") -> ScanContext:
    return ScanContext("app", rel_path, ".php", content, {}, None)


def _ids(content: str) -> list[str]:
    return [c.contract_id for c in PhpClientsDialect().extract(_ctx(content))]


class TestPhpClientsRecognition:
    def test_request_form_reads_verb_and_url_arguments(self):
        rows = list(guzzle_request_calls(GUZZLE_REQUEST))
        assert [(r.method, r.client) for r in rows] == [
            ("GET", "guzzle"),
            ("POST", "guzzle"),
        ]
        assert rows[0].url == "'/api/accounts'"

    def test_verb_form_reads_the_callee_name(self):
        rows = list(guzzle_verb_calls(GUZZLE_VERBS))
        assert [r.method for r in rows] == ["GET", "DELETE"]
        assert all(r.confidence == 0.65 for r in rows)

    def test_facade_reads_the_direct_and_the_chained_call(self):
        rows = list(laravel_http_calls(LARAVEL_HTTP))
        assert [(r.method, r.client) for r in rows] == [
            ("GET", "laravel-http"),
            ("POST", "laravel-http"),
            ("GET", "laravel-http"),
        ]

    def test_guzzle_shapes_need_a_client_in_the_file(self):
        assert list(guzzle_request_calls(UNGATED)) == []
        assert list(guzzle_verb_calls(UNGATED)) == []


class TestPhpClientsContracts:
    def test_request_form_contracts(self):
        contracts = PhpClientsDialect().extract(_ctx(GUZZLE_REQUEST))
        assert [c.contract_id for c in contracts] == [
            "http::GET::/api/accounts",
            "http::POST::/api/accounts/{param}/sync",
        ]
        assert contracts[0].meta["client"] == "guzzle"
        assert contracts[1].meta["host"] == "svc.example.com"
        assert contracts[0].confidence == 0.75

    def test_verb_form_contracts_carry_the_host_and_lower_confidence(self):
        contracts = PhpClientsDialect().extract(_ctx(GUZZLE_VERBS))
        assert [c.contract_id for c in contracts] == [
            "http::GET::/api/invoices",
            "http::DELETE::/api/invoices/1",
        ]
        assert contracts[0].meta["host"] == "billing"
        assert contracts[0].confidence == 0.65

    def test_concatenation_onto_a_folded_constant(self):
        contracts = PhpClientsDialect().extract(_ctx(GUZZLE_CONCAT))
        assert [c.contract_id for c in contracts] == ["http::GET::/api/reports"]
        assert contracts[0].meta["host"] == "reports.internal"

    def test_a_property_base_the_file_never_binds_is_stripped(self):
        (contract,) = PhpClientsDialect().extract(_ctx(GUZZLE_PROPERTY_BASE))
        assert contract.contract_id == "http::GET::/contacts"
        assert contract.meta["base_stripped"] is True
        assert contract.meta["base_token"] == "base"

    def test_facade_contracts(self):
        contracts = PhpClientsDialect().extract(_ctx(LARAVEL_HTTP))
        assert [c.contract_id for c in contracts] == [
            "http::GET::/w/api.php",
            "http::POST::/api/jobs",
            "http::GET::/api/items/{param}",
        ]
        assert {c.meta["client"] for c in contracts} == {"laravel-http"}

    def test_an_interpolated_host_leaves_no_host_in_meta(self):
        contracts = PhpClientsDialect().extract(_ctx(LARAVEL_HTTP))
        assert "host" not in contracts[0].meta


class TestPhpClientsRefusals:
    def test_every_unsettled_shape_emits_nothing(self):
        assert _ids(REFUSALS) == []

    def test_a_file_without_a_client_emits_nothing(self):
        assert _ids(UNGATED) == []

    def test_a_property_url_is_refused(self):
        source = "<?php\nuse GuzzleHttp\\Client;\n$client->request('GET', $this->uri);\n"
        assert _ids(source) == []

    def test_a_runtime_verb_is_refused(self):
        source = "<?php\nuse GuzzleHttp\\Client;\n$client->request($method, '/api/things');\n"
        assert _ids(source) == []

    def test_a_base_relative_literal_is_dropped_beside_its_rooted_siblings(self):
        # A client composes `'some/path'` onto its configured `base_uri`, so
        # the path the request reaches has a prefix the file does not carry.
        assert _ids(GUZZLE_BASE_RELATIVE) == ["http::GET::/some/path", "http::GET::/x"]

    def test_a_bare_host_carries_no_route(self):
        source = "<?php\nuse Illuminate\\Support\\Facades\\Http;\nHttp::get('example.com');\n"
        assert _ids(source) == []

    def test_a_map_lookup_beside_a_client_is_not_a_route(self):
        # A file that does construct a client still calls ``->get('key')`` on
        # other things; only an argument with a path is read.
        source = "<?php\n$client = new Client();\n$options->get('timeout');\n"
        assert _ids(source) == []

    def test_a_psr7_import_alone_does_not_gate_the_file(self):
        source = "<?php\nuse GuzzleHttp\\Psr7\\Stream;\n$adapter->get('/foo.txt');\n"
        assert _ids(source) == []


PROVIDER_SOURCE = '''"""Accounts service."""

from fastapi import FastAPI

app = FastAPI()


@app.get("/api/accounts")
def list_accounts():
    return []
'''


class TestPhpClientsCrossRepoLink:
    def test_a_php_call_links_to_a_python_route(self):
        provider_extractor = HttpExtractor()
        provider_extractor.provider_dialects = (FastApiDialect(),)
        provider_extractor.consumer_dialects = ()
        providers = provider_extractor.extract(
            Path("."), "accounts", files=[("app/main.py", ".py", PROVIDER_SOURCE)]
        )

        consumer_extractor = HttpExtractor()
        consumer_extractor.provider_dialects = ()
        consumer_extractor.consumer_dialects = (PhpClientsDialect(),)
        consumers = consumer_extractor.extract(
            Path("."), "portal", files=[("app/Sync.php", ".php", GUZZLE_REQUEST)]
        )

        links = match_contracts(providers + consumers)
        assert [(link.provider_repo, link.consumer_repo, link.contract_id) for link in links] == [
            ("accounts", "portal", "http::GET::/api/accounts")
        ]
