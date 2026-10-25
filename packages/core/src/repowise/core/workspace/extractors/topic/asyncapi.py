"""AsyncAPI documents: the channels a service sends to and receives from.

An AsyncAPI document is a contract file, read whole: a YAML or JSON file is
one only when its top-level ``asyncapi`` key names version 2 or 3.

- AsyncAPI 3: ``channels.<id>.address`` names the destination, and each
  ``operations.<op>`` points at a channel by ``$ref`` with ``action`` ``send``
  (the service publishes) or ``receive`` (it consumes).
- AsyncAPI 2: the channel key is the destination; ``subscribe`` is what the
  application sends and ``publish`` what it receives.

An address with a ``{param}`` placeholder is refused: the name it resolves to
is not in the document. The broker comes from the protocol of the channel's
servers (every server when the channel names none), mapped to the broker
names the code dialects use; a document without one protocol is ``asyncapi``,
which meets any broker (:mod:`repowise.core.workspace.matching.topic`).
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

from repowise.core.workspace.contracts import TOPIC_KIND_TOPIC
from repowise.core.workspace.origins import ContractOrigin
from repowise.core.workspace.service_names.yaml_docs import load_yaml_docs

from .dialect import topic_contract

if TYPE_CHECKING:
    from repowise.core.workspace.contracts import Contract

    from ..base import ScanContext

#: The broker named when the document does not settle one protocol.
ASYNCAPI_BROKER = "asyncapi"

#: AsyncAPI protocol names as the code dialects name the same broker, so a
#: documented Kafka topic meets a kafkajs consumer. Others are kept as written.
_PROTOCOL_BROKERS = {
    "kafka": "kafka",
    "kafka-secure": "kafka",
    "amqp": "rabbitmq",
    "amqps": "rabbitmq",
    "nats": "nats",
    "redis": "redis",
    "sqs": "sqs",
    "sns": "sns",
    "secure-mqtt": "mqtt",
}

_V3_ROLES = {"send": "provider", "receive": "consumer"}
# AsyncAPI 2 names operations from the client's side: a client subscribes to
# what the application sends.
_V2_ROLES = {"subscribe": "provider", "publish": "consumer"}

_CHANNEL_REF = "#/channels/"


def _documents(ctx: ScanContext) -> list[Any]:
    if ctx.suffix == ".json":
        try:
            return [json.loads(ctx.content)]
        except (ValueError, RecursionError):
            return []
    return load_yaml_docs(ctx.content)


def _major(doc: Any) -> str:
    """``"2"`` or ``"3"`` for an AsyncAPI document of that version, else ``""``."""
    if not isinstance(doc, Mapping):
        return ""
    version = doc.get("asyncapi")
    if not isinstance(version, str):
        return ""
    major = version.split(".", 1)[0]
    return major if major in ("2", "3") else ""


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _usable(address: Any) -> str:
    """*address* as a destination name; ``""`` when absent or parameterized."""
    if not isinstance(address, str) or not address.strip() or "{" in address:
        return ""
    return address.strip()


def _server_names(channel: Mapping[str, Any]) -> list[str]:
    """The servers *channel* names: plain names (v2) or ``$ref`` pointers (v3)."""
    out: list[str] = []
    for entry in channel.get("servers") or ():
        if isinstance(entry, str):
            out.append(entry)
        elif isinstance(entry, Mapping) and isinstance(entry.get("$ref"), str):
            out.append(entry["$ref"].rpartition("/")[2])
    return out


def _broker(doc: Mapping[str, Any], channel: Mapping[str, Any]) -> str:
    """The broker of *channel*'s servers, when they speak one protocol."""
    servers = _mapping(doc.get("servers"))
    names = _server_names(channel) or list(servers)
    raw = [_mapping(servers.get(n)).get("protocol") for n in names if n in servers]
    # A null or non-string protocol says nothing about the broker.
    protocols = {p.strip().lower() for p in raw if isinstance(p, str)}
    protocols.discard("")
    if len(protocols) != 1:
        return ASYNCAPI_BROKER
    protocol = protocols.pop()
    return _PROTOCOL_BROKERS.get(protocol, protocol)


def _channel_id(ref: Any) -> str:
    """The channel id a v3 ``channel: {$ref: '#/channels/<id>'}`` points at."""
    pointer = _mapping(ref).get("$ref")
    if not isinstance(pointer, str) or not pointer.startswith(_CHANNEL_REF):
        return ""
    token = pointer[len(_CHANNEL_REF) :]
    if "/" in token:
        return ""
    return token.replace("~1", "/").replace("~0", "~")


def _v3_rows(doc: Mapping[str, Any]) -> list[tuple[str, str, str, str]]:
    channels = _mapping(doc.get("channels"))
    rows: list[tuple[str, str, str, str]] = []
    for op_id, op in _mapping(doc.get("operations")).items():
        op = _mapping(op)
        role = _V3_ROLES.get(str(op.get("action", "")))
        channel = _mapping(channels.get(_channel_id(op.get("channel"))))
        name = _usable(channel.get("address"))
        if role and name:
            rows.append((name, role, f"operations.{op_id}", _broker(doc, channel)))
    return rows


def _v2_rows(doc: Mapping[str, Any]) -> list[tuple[str, str, str, str]]:
    rows: list[tuple[str, str, str, str]] = []
    for key, channel in _mapping(doc.get("channels")).items():
        channel = _mapping(channel)
        name = _usable(key)
        if not name:
            continue
        for operation, role in _V2_ROLES.items():
            if operation in channel:
                rows.append((name, role, operation, _broker(doc, channel)))
    return rows


def _offset(content: str, name: str) -> int:
    """Where *name* first appears in the file, for the contract's line."""
    return max(content.find(name), 0)


class AsyncApiDialect:
    """The topics an AsyncAPI 2 or 3 document declares."""

    name = "asyncapi"
    extensions = frozenset({".yaml", ".yml", ".json"})
    requires = ("asyncapi",)

    def extract(self, ctx: ScanContext) -> list[Contract]:
        out: list[Contract] = []
        for doc in _documents(ctx):
            major = _major(doc)
            if not major:
                continue
            rows = _v3_rows(doc) if major == "3" else _v2_rows(doc)
            for name, role, label, broker in rows:
                out.append(
                    topic_contract(
                        ctx,
                        name=name,
                        broker=broker,
                        kind=TOPIC_KIND_TOPIC,
                        role=role,
                        label=f"asyncapi:{label}",
                        origin=ContractOrigin.IDL,
                        offset=_offset(ctx.content, name),
                    )
                )
        return out


ASYNCAPI = AsyncApiDialect()

__all__ = ["ASYNCAPI", "ASYNCAPI_BROKER"]
