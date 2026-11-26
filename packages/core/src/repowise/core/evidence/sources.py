"""The ``evidence-source`` seam: a plugin that fetches runs instead of being handed a file.

A file format (the ``profile-format`` seam) turns bytes someone hands over into
a run. A source fetches: it reads a window of measurements from somewhere (a
database's statement statistics, a profiler service, an APM), may need
credentials only the host holds, and can return several runs of several kinds
per fetch. A plugin registers one from its ``register()``::

    from repowise.core.evidence.sources import evidence_sources

    evidence_sources.register("postgres-statements", MySource())

Every run a source returns goes through the same read, join, store and
refresh as an uploaded file (:func:`~.add.ingest_batch`), so nothing
downstream knows where a run came from: no fold imports this module.

The contract:

* :class:`SourceRequest` carries the repository's identity, the window to
  read, the opaque cursor the last stored batch returned, plain settings, and
  an opaque credentials object. Credentials are the host's: repowise passes
  the object through, never stores, logs or prints it, and a source must not
  copy it into a batch.
* :class:`SourceBatch` is one run: its header (kind, workload, class,
  environment, commit, when it was measured), then either ``repowise-evidence-v1``
  entries or raw bytes in a ``profile-format`` format, and the cursor to
  resume from once it is stored.
* Failure: raise :class:`SourceUnavailable` when the far side is down or rate
  limits (retry later from the same cursor), :class:`SourceRefused` when it
  refuses the credentials or settings (the host shows the message to the
  user). Anything else marks the fetch failed. Each batch is stored whole or
  not at all, and the cursor only moves past a batch once it has been read
  and stored (or found to join to nothing, as an upload would be).

Ceiling: ``evidence_runs.source`` is 16 characters, so a run records that it
came from a source (:data:`SOURCE_COLUMN`), not which one; a column for the
source name comes with the first migration that needs it.
"""

from __future__ import annotations

import contextlib
import json
from collections.abc import Awaitable, Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol, runtime_checkable

from repowise.core.registry.plugins import SeamRegistry

#: What ``evidence_runs.source`` records for a run a source returned.
SOURCE_COLUMN = "evidence-source"


class SourceUnavailable(Exception):  # noqa: N818 - the seam contract names it
    """The far side is down, timed out or rate limited: retry later from the same cursor."""


class SourceRefused(Exception):  # noqa: N818 - the seam contract names it
    """The far side refused the credentials or settings; the message is for the user."""


@dataclass(frozen=True)
class SourceRequest:
    """What a source reads: one repository, one window, from one cursor."""

    #: The repository's id in the store the runs go to.
    repository_id: str
    #: The repository's name or remote, for a source that keys its data by it.
    repository_name: str = ""
    #: The window to read; None leaves the bound to the source (from the cursor, or now).
    since: datetime | None = None
    until: datetime | None = None
    #: What the last stored batch returned as ``next_cursor``; None on the first fetch.
    #: Opaque to repowise: only the source reads it.
    cursor: str | None = None
    #: Plain settings (a database name, a service tag). Never secrets.
    settings: Mapping[str, Any] = field(default_factory=dict)
    #: The host's credentials object, passed through untouched. Left out of
    #: ``repr`` so a logged request never prints it.
    credentials: Any = field(default=None, repr=False, compare=False)


@dataclass(frozen=True)
class SourceBatch:
    """One run a source fetched, and the cursor to resume from once it is stored.

    Exactly one of *entries* (``repowise-evidence-v1`` entries of *kind*, with
    the document's other fields in *document*) or *data* (a file's bytes in the
    registered ``profile-format`` named *format*) is set.
    """

    kind: str
    workload: str
    #: None for a production-only kind (``route_latency``, ``statement_stats``).
    #: ``production`` is allowed here for any kind that takes it: a source is
    #: verified by the host that registered it, unlike a typed ``--class``.
    workload_class: str | None
    #: The full commit SHA measured. Only a production-only kind may leave it
    #: None when the deployed commit is unknown; it reads "deployed commit unknown".
    commit: str | None
    environment: str = "unknown"
    #: When the run was measured (for a window, its end).
    measured_at: datetime | None = None
    entries: Sequence[Mapping[str, Any]] | None = None
    #: Other v1 document fields: ``value_kind``, ``unit``, totals, ``tool``,
    #: ``window`` and ``base_commit_sha``. Never ``format``, ``kind`` or the entry list.
    document: Mapping[str, Any] = field(default_factory=dict)
    data: bytes | None = None
    format: str | None = None
    #: The clock of a profile in *data* that does not record one.
    value_kind: str | None = None
    #: Where the next fetch resumes once this batch is stored; None keeps the cursor as it was.
    next_cursor: str | None = None


@runtime_checkable
class EvidenceSource(Protocol):
    """A plugin that fetches evidence runs for a repository."""

    #: The name it registers under, e.g. ``"postgres-statements"``.
    name: str
    #: The kinds its batches carry, e.g. ``("statement_stats",)``.
    kinds: tuple[str, ...]

    def fetch(self, request: SourceRequest) -> Iterable[SourceBatch]:
        """The runs in *request*'s window after its cursor, oldest first."""
        ...


#: Source name -> source. Every registered source can be fetched; the host
#: decides which to run, when, and with which credentials.
evidence_sources: SeamRegistry[EvidenceSource] = SeamRegistry("evidence-source")


@dataclass
class FetchOutcome:
    """What one fetch did, for the host to record and show."""

    #: ``ok``, ``unavailable`` (retry later), ``refused`` (show the user) or ``failed``.
    status: str
    #: The cursor to pass next time: the last stored batch's, else the request's.
    cursor: str | None
    #: What storing each batch returned, in fetch order.
    stored: list[dict[str, Any]] = field(default_factory=list)
    #: Why the fetch stopped early; None when it read everything.
    error: str | None = None


def batch_v1(batch: SourceBatch) -> bytes:
    """*batch*'s entries as the ``repowise-evidence-v1`` file a user would upload.

    Keys sorted, so the same entries always give the same bytes, and so the
    same stored run key on a re-fetch.
    """
    from .format_v1 import EVIDENCE_FORMAT
    from .kinds import evidence_kinds

    spec = evidence_kinds.get(batch.kind)
    if spec is None:
        raise ValueError(f"unknown kind {batch.kind!r}")
    reserved = {"format", "kind", spec.entries} & set(batch.document)
    if reserved:
        raise ValueError(f"document sets {sorted(reserved)}, which the batch header owns")
    doc = {
        **batch.document,
        "format": EVIDENCE_FORMAT,
        "kind": batch.kind,
        spec.entries: list(batch.entries or ()),
    }
    return json.dumps(doc, sort_keys=True).encode("utf-8")


async def run_fetch(
    source: EvidenceSource,
    request: SourceRequest,
    store: Callable[[SourceBatch], Awaitable[dict[str, Any]]],
) -> FetchOutcome:
    """Fetch from *source* and hand each batch to *store*, under the failure contract.

    *store* stores one batch whole or not at all (in OSS, :func:`~.add.ingest_batch`
    in a savepoint) and returns what it did; a host with its own store passes
    its own. The cursor moves past a batch only once *store* returns, also when
    the batch joined to nothing (its result says ``stored: false`` and why): a
    batch is read once, as a file is added once. A batch of a kind the source
    did not declare, or one *store* refuses, fails the fetch with the batches
    before it kept. Whatever the status, the host commits what was stored
    together with :attr:`FetchOutcome.cursor`.

    Ceiling: ``fetch`` is a plain iterable, so a source that blocks on the
    network blocks this event loop for the fetch; a host serving requests on the
    same loop runs ``run_fetch`` in its own task or worker. An async protocol
    would come with a seam revision.
    """
    outcome = FetchOutcome(status="ok", cursor=request.cursor)
    batches: Any = None
    try:
        batches = iter(source.fetch(request))
        for batch in batches:
            if batch.kind not in source.kinds:
                raise ValueError(
                    f"source {source.name!r} returned a {batch.kind} batch; it declares "
                    f"{', '.join(source.kinds) or 'no kinds'}"
                )
            if (batch.entries is None) == (batch.data is None):
                raise ValueError("a batch carries exactly one of entries and data")
            outcome.stored.append(await store(batch))
            if batch.next_cursor is not None:
                outcome.cursor = batch.next_cursor
    except SourceUnavailable as exc:
        outcome.status, outcome.error = "unavailable", str(exc) or "the source is unavailable"
    except SourceRefused as exc:
        outcome.status, outcome.error = "refused", str(exc) or "the source refused the request"
    except Exception as exc:
        outcome.status, outcome.error = "failed", f"{type(exc).__name__}: {exc}"
    finally:
        # Let a generator's ``with`` blocks (a connection) close now, not at collection.
        close = getattr(batches, "close", None)
        if close is not None:
            with contextlib.suppress(Exception):
                close()
    return outcome


__all__ = [
    "SOURCE_COLUMN",
    "EvidenceSource",
    "FetchOutcome",
    "SourceBatch",
    "SourceRefused",
    "SourceRequest",
    "SourceUnavailable",
    "batch_v1",
    "evidence_sources",
    "run_fetch",
]
