"""Message topic/queue contract extraction.

Scans source files for Kafka, RabbitMQ, and NATS producer (provider) and
consumer patterns.
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from repowise.core.workspace.contracts import Contract

_log = logging.getLogger("repowise.workspace.extractors.topic")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_BLOCKED_DIRS = frozenset({
    ".git", "node_modules", "__pycache__", ".venv", "venv",
    "dist", "build", "target", "vendor", ".next", ".nuxt",
    ".tox", ".mypy_cache", ".gradle", ".mvn", "out", "bin",
})

_MAX_FILE_SIZE = 512 * 1024

_EXTENSIONS = frozenset({
    ".py", ".ts", ".tsx", ".js", ".jsx", ".java", ".go",
})


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
        role="consumer", broker="kafka", confidence=0.8,
        topic_group=1, label="@KafkaListener",
    ),
    # Java: kafkaTemplate.send("orders", ...)
    _PatternDef(
        regex=re.compile(r"""kafkaTemplate\.send\s*\(\s*['"]([^'"]+)['"]"""),
        role="provider", broker="kafka", confidence=0.8,
        topic_group=1, label="kafkaTemplate.send",
    ),
    # Node: producer.send({ topic: 'orders' })
    _PatternDef(
        regex=re.compile(r"""producer\.send\s*\(\s*\{\s*topic\s*:\s*['"]([^'"]+)['"]"""),
        role="provider", broker="kafka", confidence=0.8,
        topic_group=1, label="producer.send({topic})",
    ),
    # Node: consumer.subscribe({ topic: 'orders' })
    _PatternDef(
        regex=re.compile(r"""consumer\.subscribe\s*\(\s*\{\s*topic\s*:\s*['"]([^'"]+)['"]"""),
        role="consumer", broker="kafka", confidence=0.8,
        topic_group=1, label="consumer.subscribe({topic})",
    ),
    # Python: KafkaConsumer('orders')
    _PatternDef(
        regex=re.compile(r"""KafkaConsumer\s*\(\s*['"]([^'"]+)['"]"""),
        role="consumer", broker="kafka", confidence=0.7,
        topic_group=1, label="KafkaConsumer",
    ),
    # Python: producer.produce('orders')
    _PatternDef(
        regex=re.compile(r"""producer\.produce\s*\(\s*['"]([^'"]+)['"]"""),
        role="provider", broker="kafka", confidence=0.7,
        topic_group=1, label="producer.produce",
    ),
    # Go: ConsumePartition("orders", ...)
    _PatternDef(
        regex=re.compile(r"""ConsumePartition\s*\(\s*['"]([^'"]+)['"]"""),
        role="consumer", broker="kafka", confidence=0.7,
        topic_group=1, label="ConsumePartition",
    ),
]

# --- RabbitMQ ---

_RABBITMQ_PATTERNS: list[_PatternDef] = [
    # Java: @RabbitListener(queues = "jobs")
    _PatternDef(
        regex=re.compile(r"""@RabbitListener\s*\([^)]*queues?\s*=\s*['"]([^'"]+)['"]"""),
        role="consumer", broker="rabbitmq", confidence=0.8,
        topic_group=1, label="@RabbitListener",
    ),
    # Java: rabbitTemplate.convertAndSend("exchange", ...)
    _PatternDef(
        regex=re.compile(r"""rabbitTemplate\.convertAndSend\s*\(\s*['"]([^'"]+)['"]"""),
        role="provider", broker="rabbitmq", confidence=0.8,
        topic_group=1, label="rabbitTemplate.convertAndSend",
    ),
    # Node: channel.consume("queue", ...)
    _PatternDef(
        regex=re.compile(r"""channel\.consume\s*\(\s*['"]([^'"]+)['"]"""),
        role="consumer", broker="rabbitmq", confidence=0.8,
        topic_group=1, label="channel.consume",
    ),
    # Node: channel.publish("exchange", ...)
    _PatternDef(
        regex=re.compile(r"""channel\.publish\s*\(\s*['"]([^'"]+)['"]"""),
        role="provider", broker="rabbitmq", confidence=0.8,
        topic_group=1, label="channel.publish",
    ),
    # Node: channel.sendToQueue("queue", ...)
    _PatternDef(
        regex=re.compile(r"""channel\.sendToQueue\s*\(\s*['"]([^'"]+)['"]"""),
        role="provider", broker="rabbitmq", confidence=0.8,
        topic_group=1, label="channel.sendToQueue",
    ),
    # Python: channel.basic_consume(queue='jobs')
    _PatternDef(
        regex=re.compile(r"""channel\.basic_consume\s*\([^)]*queue\s*=\s*['"]([^'"]+)['"]"""),
        role="consumer", broker="rabbitmq", confidence=0.7,
        topic_group=1, label="basic_consume",
    ),
    # Python: channel.basic_publish(exchange='events')
    _PatternDef(
        regex=re.compile(r"""channel\.basic_publish\s*\([^)]*exchange\s*=\s*['"]([^'"]+)['"]"""),
        role="provider", broker="rabbitmq", confidence=0.7,
        topic_group=1, label="basic_publish",
    ),
]

# --- NATS ---

_NATS_PATTERNS: list[_PatternDef] = [
    # Go/Python/Node: nc.Subscribe("events") or nc.subscribe("events")
    # Requires a NATS-idiomatic variable name (nc, nats, conn, js, sub, client)
    # to avoid false-positives from RxJS, EventEmitter, custom publishers, etc.
    _PatternDef(
        regex=re.compile(r"""(?:nc|nats|conn|js|sub|client)\s*\.\s*(?:Subscribe|subscribe)\s*\(\s*['"]([^'"]+)['"]"""),
        role="consumer", broker="nats", confidence=0.8,
        topic_group=1, label="nc.Subscribe",
    ),
    # Go/Python/Node: nc.Publish("events") or nc.publish("events")
    # Requires a NATS-idiomatic variable name to avoid false-positives.
    _PatternDef(
        regex=re.compile(r"""(?:nc|nats|conn|js|sub|client)\s*\.\s*(?:Publish|publish)\s*\(\s*['"]([^'"]+)['"]"""),
        role="provider", broker="nats", confidence=0.8,
        topic_group=1, label="nc.Publish",
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
        repo_root = repo_path.resolve()
        seen: set[tuple[str, str, str]] = set()  # (file, contract_id, role) dedup

        for dirpath, dirnames, filenames in os.walk(repo_root):
            dirnames[:] = [
                d for d in dirnames
                if d not in _BLOCKED_DIRS and not d.startswith(".")
            ]
            for fname in filenames:
                fpath = Path(dirpath) / fname
                suffix = fpath.suffix.lower()
                if suffix not in _EXTENSIONS:
                    continue
                try:
                    if fpath.stat().st_size > _MAX_FILE_SIZE:
                        continue
                except OSError:
                    continue

                try:
                    content = fpath.read_text(encoding="utf-8", errors="replace")
                except OSError:
                    continue

                rel_path = fpath.relative_to(repo_root).as_posix()

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

                        contracts.append(Contract(
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
                        ))

        return contracts
