"""Every registered dialect declares what the shared engine needs.

A dialect is one module plus one registry line, and the engine
(:func:`run_dialect`) does the gating and the comment check. These tests fail
when a registered entry leaves out what that engine reads, so a new dialect
cannot skip it: a gate (tokens one of which a file must hold) on every entry,
and on every row of a table dialect, which also names its origin from the
closed vocabulary in :mod:`repowise.core.workspace.origins`.
"""

from __future__ import annotations

import pytest

from repowise.core.workspace.extractors import data, grpc, http, socket, topic
from repowise.core.workspace.extractors.base import ScanContext
from repowise.core.workspace.extractors.dialect import EVERY_FILE, run_dialect
from repowise.core.workspace.origins import ContractOrigin

REGISTERED = (
    *http.PROVIDER_DIALECTS,
    *http.CONSUMER_DIALECTS,
    *topic.DIALECTS,
    *socket.DIALECTS,
    *grpc.DIALECTS,
    *data.PROVIDER_DIALECTS,
    *data.CONSUMER_DIALECTS,
)

#: Contract file formats, where the extension alone says the file is one.
FORMAT_EXTENSIONS = frozenset({".proto", ".sql", ".prisma"})


def _ids(dialect: object) -> str:
    return f"{type(dialect).__name__}:{dialect.name}"


@pytest.mark.parametrize("dialect", REGISTERED, ids=_ids)
def test_every_dialect_declares_a_gate(dialect) -> None:
    gate = getattr(dialect, "requires", None)
    assert isinstance(gate, tuple) and gate, f"{dialect.name} declares no `requires` gate"
    assert all(isinstance(t, str) for t in gate)
    if gate == EVERY_FILE:
        assert dialect.extensions <= FORMAT_EXTENSIONS, (
            f"{dialect.name} reads source code: name the tokens a match needs"
        )
    else:
        assert all(gate), f"{dialect.name} gates on an empty token, which every file holds"


@pytest.mark.parametrize("dialect", REGISTERED, ids=_ids)
def test_every_table_row_declares_a_gate_and_an_origin(dialect) -> None:
    rows = getattr(dialect, "calls", ())
    for row in rows:
        assert row.requires, f"{dialect.name}: a row with head {row.head.pattern!r} has no gate"
        assert isinstance(row.origin, ContractOrigin), f"{dialect.name}: {row.head.pattern!r}"
    for row in getattr(dialect, "receivers", ()):  # rows read on a typed receiver
        assert row.requires, f"{dialect.name}: receiver row {row.label!r} has no gate"
        assert isinstance(row.origin, ContractOrigin), f"{dialect.name}: {row.label!r}"
    language = getattr(dialect, "_lang", None)  # a gRPC stub table
    for rule in getattr(language, "rules", ()):
        assert isinstance(rule.origin, ContractOrigin), f"{dialect.name}: {rule.pattern.pattern!r}"


def test_dialect_names_are_unique_per_contract_type() -> None:
    for registry in (
        http.PROVIDER_DIALECTS + http.CONSUMER_DIALECTS,
        topic.DIALECTS,
        socket.DIALECTS,
        grpc.DIALECTS,
        data.PROVIDER_DIALECTS + data.CONSUMER_DIALECTS,
    ):
        names = [d.name for d in registry]
        assert len(names) == len(set(names))


class _CountingText(str):
    """File text that counts each substring test made on it."""

    def __new__(cls, text: str, counts: dict[str, int]):
        self = super().__new__(cls, text)
        self.counts = counts
        return self

    def __contains__(self, token: object) -> bool:
        self.counts[token] = self.counts.get(token, 0) + 1
        return super().__contains__(token)


def test_a_token_is_searched_for_once_per_file_however_many_dialects_name_it() -> None:
    # `producer` gates the kafka dialect and two of its rows; `publish` gates
    # NATS and one of its rows. Each is looked for once.
    counts: dict[str, int] = {}
    text = _CountingText("producer.send({ topic: 'orders' })\nnc.publish('audit', x)\n", counts)
    ctx = ScanContext("svc", "src/a.ts", ".ts", text)
    for dialect in topic.DIALECTS:
        run_dialect(dialect, ctx)
    assert counts.get("producer") == 1
    assert counts.get("publish") == 1


def test_a_dialect_is_not_run_on_a_file_without_its_tokens() -> None:
    class Probe:
        name = "probe"
        extensions = frozenset({".py"})
        requires = ("needle",)
        calls = 0

        def extract(self, ctx):
            Probe.calls += 1
            return []

    run_dialect(Probe(), ScanContext("svc", "a.py", ".py", "haystack only\n"))
    assert Probe.calls == 0
    run_dialect(Probe(), ScanContext("svc", "a.py", ".py", "a needle here\n"))
    assert Probe.calls == 1
