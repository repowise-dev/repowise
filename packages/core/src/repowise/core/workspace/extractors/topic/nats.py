"""NATS publishers and subscribers.

``client.publish`` is also MQTT, Ably and every other pub/sub SDK, so a call
is read only in a file importing a NATS client, and its receiver must still
carry a NATS-idiomatic name, so an RxJS or EventEmitter ``subscribe`` beside
one is not read as a subject. A subscription subject with ``*`` or ``>`` is a
pattern the matcher expands.
"""

from __future__ import annotations

import re

from repowise.core.workspace.contracts import TOPIC_KIND_SUBJECT, TOPIC_PATTERN_NATS

from ..langs import GO, JAVA, JS_TS, PYTHON
from ..strings import Arg
from .dialect import TopicCall, TopicDialect

_RECEIVER = r"(?:nc|nats|conn|js|client)\s*\.\s*"
_LANGUAGES = GO | JAVA | JS_TS | PYTHON
# nats.go (`github.com/nats-io/...`), jnats (`io.nats.client`), nats.js and
# nats.ws (`'nats'`, `@nats-io/...`, `nats.ws`, Deno `npm:nats` / `/x/nats`)
# and nats-py (`import nats`).
_CLIENT = (
    "nats-io/", "io.nats.", "'nats'", '"nats"', "nats.ws", "npm:nats", "/x/nats", "import nats", "from nats"
)

NATS = TopicDialect(
    name="nats",
    calls=(
        TopicCall(
            head=re.compile(_RECEIVER + r"(?:Subscribe|subscribe)\s*\("),
            role="consumer",
            label="nc.Subscribe",
            extensions=_LANGUAGES,
            name=Arg(pos=0),
            kind=TOPIC_KIND_SUBJECT,
            pattern=TOPIC_PATTERN_NATS,
            requires=_CLIENT,
        ),
        TopicCall(
            head=re.compile(_RECEIVER + r"(?:Publish|publish)\s*\("),
            role="provider",
            label="nc.Publish",
            extensions=_LANGUAGES,
            name=Arg(pos=0),
            kind=TOPIC_KIND_SUBJECT,
            requires=_CLIENT,
        ),
    ),
)

__all__ = ["NATS"]
