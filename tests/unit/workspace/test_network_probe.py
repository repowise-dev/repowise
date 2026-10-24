"""The lexical probe for network calls no dialect turned into a contract."""

from __future__ import annotations

import re

from repowise.core.ingestion.languages.registry import REGISTRY
from repowise.core.workspace.network_probe import PROBE_ROWS, count_unrecognised_call_files


def test_every_row_declares_what_the_probe_needs():
    """A new row cannot skip a known language, its extensions or a compiling token."""
    languages = [row.language for row in PROBE_ROWS]
    assert languages == sorted(languages)
    assert len(set(languages)) == len(languages)
    for row in PROBE_ROWS:
        assert REGISTRY.extensions_for([row.language]), row.language
        assert row.extensions, row.language
        assert all(ext.startswith(".") and ext == ext.lower() for ext in row.extensions)
        assert row.tokens, row.language
        for token in row.tokens:
            re.compile(token)
    claimed = [ext for row in PROBE_ROWS for ext in row.extensions]
    assert len(claimed) == len(set(claimed)), "one extension, one row"


def _count(files: dict[str, str], consumers: set[str] | None = None) -> int:
    rows = [(path, "." + path.rsplit(".", 1)[1], text) for path, text in files.items()]
    return count_unrecognised_call_files(rows, consumers or set())


def test_a_client_library_no_dialect_reads_is_counted():
    files = {
        "api/cart.js": "var request = require('request');\nrequest(url, function () {});\n",
        "Orders.java": "class O { private RestTemplate rest; }\n",
        "Svc.cs": "var x = await client.GetFromJsonAsync<Item>(uri);\n",
        "kernel.php": "<?php\n$ch = curl_init($url);\n",
    }
    assert _count(files) == 4


def test_a_file_a_consumer_came_from_is_not_counted():
    files = {"api/cart.js": "const r = require('request');\n"}
    assert _count(files, {"api/cart.js"}) == 0


def test_a_token_only_in_a_comment_is_not_counted():
    files = {
        "helpers.js": "/**\n * Example: fetch('http://api.example.org/users')\n */\nmodule.exports = {};\n",
        "worker.py": "# requests.get('http://x') was removed\nimport os\n",
        "Notes.cs": "// HttpClient is injected elsewhere\nclass N {}\n",
    }
    assert _count(files) == 0


def test_a_method_named_fetch_is_not_a_network_call():
    files = {
        "store.js": "class S {\n  async fetch(id) { return this.fetch(id); }\n}\nstore.fetch(1);\n",
        "api.js": "export async function load() { return fetch('/api/x'); }\n",
    }
    assert _count(files) == 1


def test_a_fetch_method_declaration_is_not_a_call_but_real_calls_are():
    declarations = {
        "repo.js": "class Repo {\n  fetch(id) {\n    return this.db.get(id);\n  }\n}\n",
        "api.ts": "const api = {\n  fetch(id: string) { return cache[id]; },\n};\n",
    }
    assert _count(declarations) == 0
    calls = {
        "a.js": "fetch(url).then((r) => r.json());\n",
        "b.js": "const r = await fetch(url);\n",
        "c.js": "if (ok) { fetch(`/api/${id}`, { method: 'POST' }); }\n",
    }
    assert _count(calls) == 3


def test_files_with_no_network_token_or_language_are_ignored():
    files = {"util.js": "export const add = (a, b) => a + b;\n", "schema.sql": "SELECT 1;\n"}
    assert _count(files) == 0
