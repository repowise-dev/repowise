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
Tooling and build files (:func:`outside_runtime`) are skipped: a script calling
a dev server is not a call the running system misses.

:data:`MESSAGE_PROBE_ROWS` is the same probe for messaging SDKs (a broker
client, a cloud queue), counted apart so a Kafka file never enters the HTTP
denominator. A file there is excused by a topic contract of either role, since
a publish is a provider. A file that only builds a client others call counts.

Adding a client library or a language is one entry in :data:`PROBE_ROWS` or
:data:`MESSAGE_PROBE_ROWS`.
"""

from __future__ import annotations

import re
from collections.abc import Collection, Sequence
from dataclasses import dataclass
from functools import cache

from repowise.core.analysis.security.masking import mask_comments_and_strings, mask_dialect
from repowise.core.code_origin import CodeOrigin, path_origin
from repowise.core.workspace.extractors import langs
from repowise.core.workspace.extractors.base import SourceFile

#: The ``extraction_stats`` key the probe writes, per repo.
UNRECOGNISED_CALL_FILES_STAT = "call_files_unrecognised"
#: The ``extraction_stats`` key the messaging probe writes, per repo.
UNRECOGNISED_MESSAGE_FILES_STAT = "message_files_unrecognised"

#: Origins whose code runs at development or build time, never in the deployed
#: system: a script calling a dev server is no missed service call.
OUTSIDE_RUNTIME_ORIGINS: frozenset[CodeOrigin] = frozenset({"tooling", "build"})


def outside_runtime(rel_path: str) -> bool:
    """Whether :func:`..code_origin.path_origin` puts *rel_path* outside the running system.

    Read by the probe and by the diagnostics, so both leave out the same files.
    """
    return path_origin(rel_path) in OUTSIDE_RUNTIME_ORIGINS


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


_JS_MESSAGING = (
    r"kafkajs|node-rdkafka|@confluentinc/kafka-javascript|@aws-sdk/client-sqs|@aws-sdk/client-sns"
    r"|sqs-consumer|sqs-producer|@google-cloud/pubsub|@azure/service-bus|@azure/event-hubs"
    r"|amqplib|amqp-connection-manager|nats|mqtt|@dapr/dapr|@temporalio/client|@temporalio/worker"
)
_PY_MESSAGING = (
    r"kafka|confluent_kafka|aiokafka|pika|aio_pika|nats|paho\.mqtt|aiomqtt"
    r"|google\.cloud\.pubsub(?:_v1)?|azure\.servicebus|azure\.eventhub|dapr\.clients|temporalio"
)
_JVM_MESSAGING = (
    r"org\.apache\.kafka|org\.springframework\.kafka|org\.springframework\.cloud\.stream"
    r"|software\.amazon\.awssdk\.services\.(?:sqs|sns)|com\.amazonaws\.services\.(?:sqs|sns)"
    r"|io\.awspring\.cloud\.(?:sqs|sns)|com\.google\.cloud\.pubsub|com\.google\.cloud\.spring\.pubsub"
    r"|com\.azure\.messaging\.(?:servicebus|eventhubs)|com\.rabbitmq\.client|org\.springframework\.amqp"
    r"|(?:javax|jakarta)\.jms|io\.nats|org\.eclipse\.paho|io\.dapr|io\.temporal"
)
_GO_MESSAGING = (
    r"github\.com/(?:segmentio/kafka-go|confluentinc/confluent-kafka-go|IBM/sarama|Shopify/sarama"
    r"|twmb/franz-go|nats-io/nats\.go|eclipse/paho\.mqtt\.golang|rabbitmq/amqp091-go|streadway/amqp"
    r"|dapr/go-sdk|aws/aws-sdk-go(?:-v2)?/service/(?:sqs|sns)"
    r"|Azure/azure-sdk-for-go/sdk/messaging/(?:azservicebus|azeventhubs))"
    r"|cloud\.google\.com/go/pubsub|go\.temporal\.io/sdk"
)

#: Messaging SDK tokens, one row per language: Kafka, SQS/SNS, Pub/Sub, Service
#: Bus, Event Hubs, RabbitMQ, JMS, NATS, MQTT, Redis streams, Dapr, Temporal.
#: Imports, mostly, so a word in prose does not count; Redis appears only by
#: its stream verbs, since a Redis cache is no message.
MESSAGE_PROBE_ROWS: tuple[ProbeRow, ...] = (
    ProbeRow(
        "csharp",
        langs.CSHARP | langs.VBNET,
        (
            r"\b(?:using|Imports)\s+(?:Confluent\.Kafka|Amazon\.SQS|Amazon\.SimpleNotificationService"
            r"|Google\.Cloud\.PubSub|Azure\.Messaging\.(?:ServiceBus|EventHubs)|RabbitMQ\.Client"
            r"|NATS\.(?:Client|Net)|MQTTnet|Dapr\.Client|Temporalio)\b",
            r"\bStream(?:Add|ReadGroup)(?:Async)?\s*\(",
        ),
    ),
    ProbeRow(
        "go",
        langs.GO,
        (rf"\"(?:{_GO_MESSAGING})[/\"]", r"\.(?:XAdd|XReadGroup)\s*\("),
    ),
    ProbeRow(
        "java",
        langs.JAVA | langs.KOTLIN,
        (rf"\bimport\s+(?:{_JVM_MESSAGING})\b", r"\bopsForStream\s*\("),
    ),
    ProbeRow(
        "javascript",
        langs.JS_TS,
        (
            rf"(?:\brequire\(\s*|\bfrom\s+|\bimport\s+)['\"](?:{_JS_MESSAGING})(?:/[^'\"\n]*)?['\"]",
            r"\bnew\s+(?:AWS\.)?(?:SQS|SNS)\s*\(",
            r"\.(?:xadd|xreadgroup|xAdd|xReadGroup)\s*\(",
        ),
    ),
    ProbeRow(
        "php",
        langs.PHP,
        (r"\b(?:PhpAmqpLib|RdKafka|Aws\\Sqs|Aws\\Sns|Google\\Cloud\\PubSub)\\",),
        hash_comments=True,
    ),
    ProbeRow(
        "python",
        langs.PYTHON,
        (
            rf"(?m:^[ \t]*(?:from|import)[ \t]+(?:{_PY_MESSAGING})\b)",
            r"(?m:^[ \t]*from[ \t]+google\.cloud[ \t]+import[ \t][^\n]*\bpubsub)",
            r"\bboto3\.(?:client|resource)\(\s*['\"](?:sqs|sns)['\"]",
            r"\.(?:xadd|xreadgroup)\s*\(",
        ),
        hash_comments=True,
    ),
    ProbeRow(
        "ruby",
        langs.RUBY,
        (
            r"\brequire\s*\(?\s*['\"](?:kafka|rdkafka|bunny|aws-sdk-sqs|aws-sdk-sns"
            r"|google/cloud/pubsub|nats/client|mqtt)['\"]",
        ),
        hash_comments=True,
    ),
    ProbeRow(
        "rust",
        langs.RUST,
        (r"\b(?:rdkafka|lapin|async_nats|rumqttc|google_cloud_pubsub|aws_sdk_sqs|aws_sdk_sns)::",),
    ),
)


@cache
def _by_extension(rows: tuple[ProbeRow, ...]) -> dict[str, tuple[re.Pattern[str], bool]]:
    """One compiled alternation per extension of *rows*, built once per process."""
    table: dict[str, tuple[re.Pattern[str], bool]] = {}
    for row in rows:
        pattern = re.compile("|".join(f"(?:{t})" for t in row.tokens))
        for ext in row.extensions:
            table[ext] = (pattern, row.hash_comments)
    return table


def count_unrecognised_call_files(
    files: Sequence[SourceFile],
    consumer_files: Collection[str],
    rows: tuple[ProbeRow, ...] = PROBE_ROWS,
) -> int:
    """Files in *files* with a token of *rows* and no contract read from them.

    Tooling and build files (:func:`outside_runtime`) are not counted.

    *consumer_files* are the paths that excuse a file: a consumer contract's
    for :data:`PROBE_ROWS`, any topic contract's for :data:`MESSAGE_PROBE_ROWS`.
    One regex search per remaining file of a probed language; only a hit pays
    for the comment mask that confirms it.
    """
    table = _by_extension(rows)
    count = 0
    for rel_path, suffix, content in files:
        entry = table.get(suffix)
        if entry is None or rel_path in consumer_files or outside_runtime(rel_path):
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
