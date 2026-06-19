"""Message topic/queue contract extraction.

Scans source files for Kafka, RabbitMQ, and NATS producer (provider) and
consumer patterns.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from repowise.core.ingestion.languages.registry import REGISTRY as _LANG_REGISTRY

from .base import iter_source_files

if TYPE_CHECKING:
    from repowise.core.workspace.contracts import Contract

_log = logging.getLogger("repowise.workspace.extractors.topic")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_EXTENSIONS = _LANG_REGISTRY.extensions_for(["python", "typescript", "javascript", "java", "go"])


# ---------------------------------------------------------------------------
# Pattern definitions
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _PatternDef:
    regex: re.Pattern[str]
    role: str  # "provider" | "consumer"
    broker: str  # "kafka" | "rabbitmq" | "nats"
    confidence: float
    topic_group: int  # capture group index for topic name
    label: str  # human-readable name


# --- Kafka ---

_KAFKA_PATTERNS: list[_PatternDef] = [
    # Java: @KafkaListener(topics = "orders")
    _PatternDef(
        regex=re.compile(r"""@KafkaListener\s*\([^)]*topics?\s*=\s*['"]([^'"]+)['"]"""),
        role="consumer",
        broker="kafka",
        confidence=0.8,
        topic_group=1,
        label="@KafkaListener",
    ),
    # Java: kafkaTemplate.send("orders", ...)
    _PatternDef(
        regex=re.compile(r"""kafkaTemplate\.send\s*\(\s*['"]([^'"]+)['"]"""),
        role="provider",
        broker="kafka",
        confidence=0.8,
        topic_group=1,
        label="kafkaTemplate.send",
    ),
    # Node: producer.send({ topic: 'orders' })
    _PatternDef(
        regex=re.compile(r"""producer\.send\s*\(\s*\{\s*topic\s*:\s*['"]([^'"]+)['"]"""),
        role="provider",
        broker="kafka",
        confidence=0.8,
        topic_group=1,
        label="producer.send({topic})",
    ),
    # Node: consumer.subscribe({ topic: 'orders' })
    _PatternDef(
        regex=re.compile(r"""consumer\.subscribe\s*\(\s*\{\s*topic\s*:\s*['"]([^'"]+)['"]"""),
        role="consumer",
        broker="kafka",
        confidence=0.8,
        topic_group=1,
        label="consumer.subscribe({topic})",
    ),
    # Python: KafkaConsumer('orders')
    _PatternDef(
        regex=re.compile(r"""KafkaConsumer\s*\(\s*['"]([^'"]+)['"]"""),
        role="consumer",
        broker="kafka",
        confidence=0.7,
        topic_group=1,
        label="KafkaConsumer",
    ),
    # Python: producer.produce('orders')
    _PatternDef(
        regex=re.compile(r"""producer\.produce\s*\(\s*['"]([^'"]+)['"]"""),
        role="provider",
        broker="kafka",
        confidence=0.7,
        topic_group=1,
        label="producer.produce",
    ),
    # Go: ConsumePartition("orders", ...)
    _PatternDef(
        regex=re.compile(r"""ConsumePartition\s*\(\s*['"]([^'"]+)['"]"""),
        role="consumer",
        broker="kafka",
        confidence=0.7,
        topic_group=1,
        label="ConsumePartition",
    ),
]

# --- RabbitMQ ---

_RABBITMQ_PATTERNS: list[_PatternDef] = [
    # Java: @RabbitListener(queues = "jobs")
    _PatternDef(
        regex=re.compile(r"""@RabbitListener\s*\([^)]*queues?\s*=\s*['"]([^'"]+)['"]"""),
        role="consumer",
        broker="rabbitmq",
        confidence=0.8,
        topic_group=1,
        label="@RabbitListener",
    ),
    # Java: rabbitTemplate.convertAndSend("exchange", ...)
    _PatternDef(
        regex=re.compile(r"""rabbitTemplate\.convertAndSend\s*\(\s*['"]([^'"]+)['"]"""),
        role="provider",
        broker="rabbitmq",
        confidence=0.8,
        topic_group=1,
        label="rabbitTemplate.convertAndSend",
    ),
    # Node: channel.consume("queue", ...)
    _PatternDef(
        regex=re.compile(r"""channel\.consume\s*\(\s*['"]([^'"]+)['"]"""),
        role="consumer",
        broker="rabbitmq",
        confidence=0.8,
        topic_group=1,
        label="channel.consume",
    ),
    # Node: channel.publish("exchange", ...)
    _PatternDef(
        regex=re.compile(r"""channel\.publish\s*\(\s*['"]([^'"]+)['"]"""),
        role="provider",
        broker="rabbitmq",
        confidence=0.8,
        topic_group=1,
        label="channel.publish",
    ),
    # Node: channel.sendToQueue("queue", ...)
    _PatternDef(
        regex=re.compile(r"""channel\.sendToQueue\s*\(\s*['"]([^'"]+)['"]"""),
        role="provider",
        broker="rabbitmq",
        confidence=0.8,
        topic_group=1,
        label="channel.sendToQueue",
    ),
    # Python: channel.basic_consume(queue='jobs')
    _PatternDef(
        regex=re.compile(r"""channel\.basic_consume\s*\([^)]*queue\s*=\s*['"]([^'"]+)['"]"""),
        role="consumer",
        broker="rabbitmq",
        confidence=0.7,
        topic_group=1,
        label="basic_consume",
    ),
    # Python: channel.basic_publish(exchange='events')
    _PatternDef(
        regex=re.compile(r"""channel\.basic_publish\s*\([^)]*exchange\s*=\s*['"]([^'"]+)['"]"""),
        role="provider",
        broker="rabbitmq",
        confidence=0.7,
        topic_group=1,
        label="basic_publish",
    ),
]

# --- NATS ---

_NATS_PATTERNS: list[_PatternDef] = [
    # Go/Python/Node: nc.Subscribe("events") or nc.subscribe("events")
    # Requires a NATS-idiomatic variable name (nc, nats, conn, js, sub, client)
    # to avoid false-positives from RxJS, EventEmitter, custom publishers, etc.
    _PatternDef(
        regex=re.compile(
            r"""(?:nc|nats|conn|js|sub|client)\s*\.\s*(?:Subscribe|subscribe)\s*\(\s*['"]([^'"]+)['"]"""
        ),
        role="consumer",
        broker="nats",
        confidence=0.8,
        topic_group=1,
        label="nc.Subscribe",
    ),
    # Go/Python/Node: nc.Publish("events") or nc.publish("events")
    # Requires a NATS-idiomatic variable name to avoid false-positives.
    _PatternDef(
        regex=re.compile(
            r"""(?:nc|nats|conn|js|sub|client)\s*\.\s*(?:Publish|publish)\s*\(\s*['"]([^'"]+)['"]"""
        ),
        role="provider",
        broker="nats",
        confidence=0.8,
        topic_group=1,
        label="nc.Publish",
    ),
]

_ALL_PATTERNS = _KAFKA_PATTERNS + _RABBITMQ_PATTERNS + _NATS_PATTERNS


# ---------------------------------------------------------------------------
# Extractor
# ---------------------------------------------------------------------------


class TopicExtractor:
    """Extract message topic/queue contracts from source files."""

    def extract(self, repo_path: Path, repo_alias: str = "") -> list[Contract]:
        from repowise.core.workspace.contracts import Contract

        contracts: list[Contract] = []
        seen: set[tuple[str, str, str]] = set()  # (file, contract_id, role) dedup

        # File discovery is gitignore- and nested-repo-aware (see
        # ``iter_source_files``) so a workspace repo whose root contains nested
        # repos or large ignored trees is not scanned end-to-end.
        for rel_path, _suffix, content in iter_source_files(repo_path, _EXTENSIONS):
            for pdef in _ALL_PATTERNS:
                for match in pdef.regex.finditer(content):
                    topic_name = match.group(pdef.topic_group).strip()
                    if not topic_name:
                        continue

                    contract_id = f"topic::{topic_name.lower()}"

                    # Deduplicate within the same file
                    dedup_key = (rel_path, contract_id, pdef.role)
                    if dedup_key in seen:
                        continue
                    seen.add(dedup_key)

                    contracts.append(
                        Contract(
                            repo=repo_alias,
                            contract_id=contract_id,
                            contract_type="topic",
                            role=pdef.role,
                            file_path=rel_path,
                            symbol_name=f"{pdef.label}('{topic_name}')",
                            confidence=pdef.confidence,
                            service=None,
                            meta={
                                "topic": topic_name,
                                "broker": pdef.broker,
                            },
                        )
                    )

        return contracts
