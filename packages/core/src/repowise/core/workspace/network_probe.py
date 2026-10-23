"""Count source files that make network calls no contract was read from.

Coverage over the calls a dialect located is a ratio over what extraction
already saw: a repo whose client library no dialect knows locates nothing and
reads as 100%. This probe is the cheap other half. Over the files the one
extraction walk already holds in memory, it counts those that carry a
network-call token (a client library, a channel, a raw request) yet produced no
consumer contract. Each such file is at least one call the graph does not have.

Lexical by design: it never names an endpoint or makes a contract, it only
says that something was missed and where. Ceilings, stated so they read as
intent: only extensions some extractor walks are probed; a token in a string
literal still counts (comments are masked); a file whose calls were located but
not resolved can be counted here and in ``http_consumer_unresolved`` both.

Adding a client library or a language is one entry in :data:`PROBE_ROWS`.
"""

from __future__ import annotations

import re
from collections.abc import Collection, Sequence
from dataclasses import dataclass
from functools import cache

from repowise.core.analysis.security.masking import mask_comments_and_strings, mask_dialect
from repowise.core.workspace.extractors import langs
from repowise.core.workspace.extractors.base import SourceFile

#: The ``extraction_stats`` key the probe writes, per repo.
UNRECOGNISED_CALL_FILES_STAT = "call_files_unrecognised"


@dataclass(frozen=True)
class ProbeRow:
    """The network-call tokens of one language, as data."""

    language: str
    extensions: frozenset[str]
    #: Regexes, any of which marks a file as making a network call.
    tokens: tuple[str, ...]
    #: Whether ``#`` opens a comment in this language.
    hash_comments: bool = False


_JS_LIBS = r"request|request-promise(?:-native)?|superagent|needle|node-fetch|undici|got|axios|ky|ofetch|cross-fetch|isomorphic-fetch"

#: One row per language, sorted by language.
PROBE_ROWS: tuple[ProbeRow, ...] = (
    ProbeRow(
        "csharp",
        langs.CSHARP | langs.VBNET,
        (
            r"\bHttpClient\b",
            r"\bHttpRequestMessage\b",
            r"\b(?:Get|Post|Put|Patch|Delete)(?:FromJson|AsJson)Async\b",
            r"\bGrpcChannel\b",
            r"\bAddGrpcClient\b",
            r"\bRestSharp\b",
        ),
    ),
    ProbeRow(
        "go",
        langs.GO,
        (
            r"\bhttp\.(?:Get|Post|PostForm|Head|NewRequest(?:WithContext)?)\s*\(",
            r"\bgrpc\.(?:Dial|DialContext|NewClient)\s*\(",
            r"\bresty\.New\s*\(",
        ),
    ),
    ProbeRow(
        "java",
        langs.JAVA | langs.KOTLIN,
        (
            r"\bRestTemplate\b",
            r"\bWebClient\b",
            r"\bRestClient\b",
            r"@FeignClient\b",
            r"\bOkHttpClient\b",
            r"\bHttpURLConnection\b",
            r"\bHttpClient\.new",
            r"\bManagedChannelBuilder\b",
        ),
    ),
    ProbeRow(
        "javascript",
        langs.JS_TS,
        (
            rf"\brequire\(\s*['\"](?:{_JS_LIBS})['\"]\s*\)",
            rf"\bfrom\s+['\"](?:{_JS_LIBS})['\"]",
            # The global, not a method named fetch: no `this.fetch(` call and no
            # declaration (`async fetch(id) {`, `fetch(id) {`, whose `)` meets `{`).
            r"(?<![\w.$])(?<!function )(?<!async )fetch\s*\((?![^()\n]*\)\s*\{)",
            r"\$http\s*[.(]",
            r"\bnew\s+XMLHttpRequest\b",
            r"\bhttps?\.(?:request|get)\s*\(",
        ),
    ),
    ProbeRow(
        "php",
        langs.PHP,
        (
            r"\bcurl_init\s*\(",
            r"\bHttp::(?:get|post|put|patch|delete|withHeaders|acceptJson|baseUrl)\b",
            r"\bGuzzleHttp\\",
            r"\bfile_get_contents\s*\(\s*['\"]https?://",
        ),
        hash_comments=True,
    ),
    ProbeRow(
        "python",
        langs.PYTHON,
        (
            r"\brequests\.(?:get|post|put|patch|delete|head|request|Session)\b",
            r"\bhttpx\.(?:get|post|put|patch|delete|request|Client|AsyncClient)\b",
            r"\baiohttp\.ClientSession\b",
            r"\burllib\.request\.urlopen\b",
            r"\bgrpc\.(?:insecure_|secure_)channel\b",
        ),
        hash_comments=True,
    ),
    ProbeRow(
        "ruby",
        langs.RUBY,
        (
            r"\bNet::HTTP\b",
            r"\bFaraday\b",
            r"\bHTTParty\b",
            r"\bRestClient\b",
        ),
        hash_comments=True,
    ),
    ProbeRow(
        "rust",
        langs.RUST,
        (
            r"\breqwest::",
            r"\bhyper::Client\b",
            r"\bureq::",
            r"\btonic::transport::Channel\b",
        ),
    ),
)


@cache
def _by_extension() -> dict[str, tuple[re.Pattern[str], bool]]:
    """One compiled alternation per extension, built once per process."""
    table: dict[str, tuple[re.Pattern[str], bool]] = {}
    for row in PROBE_ROWS:
        pattern = re.compile("|".join(f"(?:{t})" for t in row.tokens))
        for ext in row.extensions:
            table[ext] = (pattern, row.hash_comments)
    return table


def count_unrecognised_call_files(
    files: Sequence[SourceFile], consumer_files: Collection[str]
) -> int:
    """Files in *files* with a network-call token and no consumer contract.

    *consumer_files* are the paths a consumer contract came from. One regex
    search per remaining file of a probed language; only a hit pays for the
    comment mask that confirms it.
    """
    table = _by_extension()
    count = 0
    for rel_path, suffix, content in files:
        entry = table.get(suffix)
        if entry is None or rel_path in consumer_files:
            continue
        pattern, hash_comments = entry
        if not pattern.search(content):
            continue
        masked = mask_comments_and_strings(
            content, strings=False, hash_comments=hash_comments, **mask_dialect(rel_path)
        )
        if pattern.search(masked):
            count += 1
    return count
