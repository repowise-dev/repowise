"""The lexical probe for network calls no dialect turned into a contract."""

from __future__ import annotations

import re

import pytest

from repowise.core.ingestion.languages.registry import REGISTRY
from repowise.core.workspace.network_probe import (
    MESSAGE_PROBE_ROWS,
    PROBE_ROWS,
    count_unrecognised_call_files,
)


@pytest.mark.parametrize("rows", [PROBE_ROWS, MESSAGE_PROBE_ROWS], ids=["http", "messaging"])
def test_every_row_declares_what_the_probe_needs(rows):
    """A new row cannot skip a known language, its extensions or a compiling token."""
    languages = [row.language for row in rows]
    assert languages == sorted(languages)
    assert len(set(languages)) == len(languages)
    for row in rows:
        assert REGISTRY.extensions_for([row.language]), row.language
        assert row.extensions, row.language
        assert all(ext.startswith(".") and ext == ext.lower() for ext in row.extensions)
        assert row.tokens, row.language
        for token in row.tokens:
            re.compile(token)
    claimed = [ext for row in rows for ext in row.extensions]
    assert len(claimed) == len(set(claimed)), "one extension, one row"


def _count(files: dict[str, str], consumers: set[str] | None = None, rows=PROBE_ROWS) -> int:
    walked = [(path, "." + path.rsplit(".", 1)[1], text) for path, text in files.items()]
    return count_unrecognised_call_files(walked, consumers or set(), rows)


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


def test_tooling_and_build_files_are_not_counted():
    # A script polling a dev server, or a build config, is no missed service call.
    files = {
        "scripts/warm_cache.py": "import httpx\nhttpx.get(url)\n",
        ".github/scripts/notify.js": "fetch(url);\n",
        "src/client.js": "fetch(url);\n",
    }
    assert _count(files) == 1


def test_a_messaging_sdk_no_dialect_reads_is_counted_apart_from_http():
    files = {
        "photos/index.js": "const { PubSub } = require('@google-cloud/pubsub');\n",
        "thumbs/main.py": "from google.cloud import pubsub_v1\n",
        "bus/Sender.cs": "using Azure.Messaging.ServiceBus;\n",
        "events/pub.go": 'import (\n\t"cloud.google.com/go/pubsub"\n)\n',
        "Orders.kt": "import org.springframework.cloud.stream.function.StreamBridge\n",
        "stream.ts": "await redis.xadd('orders', '*', 'id', id);\n",
        "Kernel.php": "<?php\nuse PhpAmqpLib\\Connection\\AMQPStreamConnection;\n",
    }
    assert _count(files, rows=MESSAGE_PROBE_ROWS) == 7
    assert _count(files) == 0  # never in the HTTP denominator


def test_a_messaging_file_a_topic_contract_came_from_is_not_counted():
    files = {"queue.py": "import pika\n", "worker.py": "from kafka import KafkaConsumer\n"}
    assert _count(files, {"queue.py", "worker.py"}, MESSAGE_PROBE_ROWS) == 0


def test_messaging_words_in_prose_comments_or_caches_are_not_counted():
    files = {
        "log.py": "# from kafka import KafkaProducer\nprint('read from nats')\n",
        "cache.js": "const Redis = require('ioredis');\nredis.get('k');\n",
        "natsy.py": "import natsort\n",
        "Notes.java": "// import org.apache.kafka.clients.producer.KafkaProducer;\nclass N {}\n",
    }
    assert _count(files, rows=MESSAGE_PROBE_ROWS) == 0


def test_the_extraction_counts_both_probes_and_only_messaging_when_topics_are_read():
    from repowise.core.workspace.contracts import Contract, _probe_misses

    files = [
        ("pub.py", ".py", "import pika\n"),
        ("sub.py", ".py", "from kafka import KafkaConsumer\n"),
        ("api.py", ".py", "import requests\nrequests.get(url)\n"),
    ]
    topic = Contract(repo="r", contract_id="topic::orders", contract_type="topic",
                     role="provider", file_path="pub.py", symbol_name="f", confidence=0.8)
    assert _probe_misses(files, [topic], True) == {
        "call_files_unrecognised": 1,
        "message_files_unrecognised": 1,
    }
    assert _probe_misses(files, [topic], False) == {"call_files_unrecognised": 1}
