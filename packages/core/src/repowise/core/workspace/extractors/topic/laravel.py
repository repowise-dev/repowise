"""Laravel queued jobs: where each is dispatched, and the queue it runs on.

A job reaches a queue two ways. The dispatch site can name it
(``NotifyTicketSold::dispatch($t)->onQueue('ticket.sold')``,
``dispatch((new X)->onQueue('q'))``, ``Queue::pushOn('q', new X)``,
``$schedule->job(new X, 'q')``), or the job class can declare it
(``public $queue = 'q'``, ``$this->onQueue('q')`` in its constructor, a
queued listener's ``viaQueue()``). Each dispatch site is a provider of its
queue and each ``ShouldQueue`` class a consumer of every queue it runs on.

The two meet across files, so this dialect is a repo pass: a site that names
no queue takes the one its job class declares, and a class runs on the
queues its dispatch sites name. Classes are joined by fully qualified name,
qualified the way the import graph qualifies them
(:func:`...ingestion.languages.php_same_namespace.php_name_qualifier`). A job
on the connection's default queue names no queue here and is not recorded:
every app has a ``default`` queue, and linking them all would be noise.
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from repowise.core.ingestion.framework_routes import LARAVEL_SCHEDULED_JOB
from repowise.core.ingestion.languages.php_same_namespace import (
    PHP_CLASS_DECL_RE,
    PHP_CLASS_NAME,
    file_namespace,
    php_name_qualifier,
    qualify_php_name,
)
from repowise.core.workspace.contracts import TOPIC_KIND_QUEUE

from ..calls import FileStrings, call_chain
from ..langs import PHP
from ..strings import PHP_SYNTAX, Arg, call_arguments, match_paren
from .dialect import topic_contract

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

    from repowise.core.workspace.contracts import Contract

    from ..base import ScanContext

_BROKER = "laravel"
_NAME = PHP_CLASS_NAME
# Every app has this queue: two apps naming it share nothing.
_DEFAULT_QUEUE = "default"

# A file must contain one of these for any pattern below to run.
_HINTS = ("dispatch", "Queue::", "ShouldQueue", "job")

# Literal-led heads; the text before a `dispatch(` says which form it is.
_DISPATCH_RE = re.compile(r"dispatch(?:If|Unless)?\s*\(")
_STATIC_OWNER_RE = re.compile(rf"(?P<cls>{_NAME})\s*::\s*$")
_QUEUE_FACADE_RE = re.compile(
    r"Queue::\s*(?:connection\s*\([^()]*\)\s*->\s*)?(?P<verb>pushOn|push|laterOn|later|bulk)\s*\("
)
_SCHEDULED_JOB_RE = re.compile(LARAVEL_SCHEDULED_JOB + r"\s*\(")
_ON_QUEUE_RE = re.compile(r"->\s*onQueue\s*\(", re.IGNORECASE)
# PHP method names are case-insensitive.
_ON_QUEUE_LINKS = frozenset({"onqueue", "allonqueue"})

# `(queue position, job position)` of each `Queue::` call.
_QUEUE_FACADE_ARGS = {
    "push": (2, 0),
    "pushOn": (0, 1),
    "later": (3, 1),
    "laterOn": (0, 2),
    "bulk": (2, None),
}

# A job argument: `new X(...)`, `(new X)->onQueue(...)`, or `X::class`.
_JOB_ARG_RE = re.compile(rf"^\(?\s*new\s+(?P<new>{_NAME})|^(?P<ref>{_NAME})\s*::\s*class\s*$")

_QUEUED_CLASS_RE = re.compile(
    PHP_CLASS_DECL_RE.pattern + r"[^{;]*?\bShouldQueue\b", re.MULTILINE
)
# A class's own queue: `public $queue = 'q';` / `$this->queue = 'q';`, and a
# queued listener's `viaQueue(): string { return 'q'; }`.
_QUEUE_PROPERTY_RE = re.compile(r"(?:\$|->\s*)queue\s*=(?![=>])\s*(?P<value>[^;]+);")
_VIA_QUEUE_RE = re.compile(r"function\s+viaQueue\s*\([^)]*\)[^{]*\{\s*return\s+(?P<value>[^;]+);")
_THIS_ON_QUEUE_RE = re.compile(r"\$this\s*->\s*onQueue\s*\(")

# Static `X::dispatch` owners that are not job classes.
_NOT_JOBS = frozenset({"Event", "Bus", "self", "static", "parent"})


@dataclass
class _Site:
    ctx: ScanContext
    offset: int
    label: str
    job: str | None
    queue: str | None


@dataclass
class _Job:
    ctx: ScanContext
    offset: int
    name: str
    queues: set[str] = field(default_factory=set)


def _short(name: str) -> str:
    return name.rpartition("\\")[2]


def _job_name(arg: str) -> str | None:
    m = _JOB_ARG_RE.match(arg.strip())
    return (m.group("new") or m.group("ref")) if m else None


class _FileReader:
    """One PHP file's queue facts, its strings and names read on first use."""

    def __init__(self, ctx: ScanContext) -> None:
        self.ctx = ctx
        self.content = ctx.content
        self.strings = FileStrings(ctx.content, PHP_SYNTAX)
        self._qualify: Callable[[str], str] | None = None

    def qualify(self, name: str) -> str:
        if self._qualify is None:
            self._qualify = php_name_qualifier(self.ctx.rel_path, self.content)
        return self._qualify(name)

    def queue(self, args: list[str], pos: int = 0) -> str | None:
        """The queue *args* names at *pos*; ``""`` when it names one this file cannot settle."""
        if pos >= len(args):
            return None
        values, _ = self.strings.resolve(args, Arg(pos=pos))
        return values[0] if values else ""

    def chained_queue(self, close: int) -> str | None:
        for link in call_chain(self.content, close):
            if link.name.lower() in _ON_QUEUE_LINKS:
                return self.queue(link.args)
        return None

    def inner_queue(self, arg: str) -> str | None:
        """The queue of ``(new X(...))->onQueue('q')`` passed as an argument."""
        m = _ON_QUEUE_RE.search(arg)
        if m is None:
            return None
        return self.queue(call_arguments(arg, m.end() - 1) or [])

    def site(self, offset: int, label: str, job: str | None, queue: str | None) -> _Site | None:
        if queue == "":
            return None  # named a queue this file cannot settle: never guess another
        return _Site(self.ctx, offset, label, self.qualify(job) if job else None, queue)

    def dispatches(self) -> Iterator[_Site]:
        content = self.content
        for m in _DISPATCH_RE.finditer(content):
            start = m.start()
            before = content[max(0, start - 200) : start]
            if before.endswith("::") or before.rstrip().endswith("::"):
                owner = _STATIC_OWNER_RE.search(before)
                if owner is None:
                    continue
                cls = owner.group("cls")
                static = _short(cls) not in _NOT_JOBS
            elif start and (content[start - 1].isalnum() or content[start - 1] in "_$"):
                continue  # `redispatch(`, `$dispatch(`
            elif before.rstrip().endswith("function"):
                continue
            else:
                static = False
            close = match_paren(content, m.end() - 1)
            if close < 0:
                continue
            args = call_arguments(content, m.end() - 1, close) or []
            if static:
                job, label = cls, f"{_short(cls)}::dispatch"
                queue = self.chained_queue(close)
            else:
                # `dispatch(new X)`, `Bus::dispatch(...)`, `$this->dispatch(...)`.
                job = _job_name(args[0]) if args else None
                if job is None:
                    continue  # a Livewire or browser event, not a job
                label = "dispatch"
                queue = self.chained_queue(close)
                if queue is None:
                    queue = self.inner_queue(args[0])
            site = self.site(start, label, job, queue)
            if site is not None:
                yield site

    def facade_pushes(self) -> Iterator[_Site]:
        for m in _QUEUE_FACADE_RE.finditer(self.content):
            verb = m.group("verb")
            args = call_arguments(self.content, m.end() - 1) or []
            queue_pos, job_pos = _QUEUE_FACADE_ARGS[verb]
            job = _job_name(args[job_pos]) if job_pos is not None and job_pos < len(args) else None
            site = self.site(m.start(), f"Queue::{verb}", job, self.queue(args, queue_pos))
            if site is not None:
                yield site

    def scheduled_jobs(self) -> Iterator[_Site]:
        for m in _SCHEDULED_JOB_RE.finditer(self.content):
            args = call_arguments(self.content, m.end() - 1) or []
            job = _job_name(args[0]) if args else None
            if job is None:
                continue
            site = self.site(m.start(), "schedule->job", job, self.queue(args, 1))
            if site is not None:
                yield site

    def queued_class(self) -> _Job | None:
        m = _QUEUED_CLASS_RE.search(self.content)
        if m is None:
            return None
        name = qualify_php_name(m.group("cls"), file_namespace(self.content), {})
        job = _Job(self.ctx, m.start("cls"), name)
        body = self.content[m.end() :]
        values = [v.group("value") for r in (_QUEUE_PROPERTY_RE, _VIA_QUEUE_RE) for v in r.finditer(body)]
        values.extend(
            (call_arguments(body, t.end() - 1) or [""])[0] for t in _THIS_ON_QUEUE_RE.finditer(body)
        )
        for value in values:
            queue = self.queue([value])
            if queue and queue != _DEFAULT_QUEUE:
                job.queues.add(queue)
        return job


class _LaravelQueuePass:
    def __init__(self) -> None:
        self.sites: list[_Site] = []
        self.jobs: dict[str, _Job] = {}

    def scan(self, ctx: ScanContext) -> None:
        content = ctx.content
        if not any(h in content for h in _HINTS):
            return
        reader = _FileReader(ctx)
        if "ShouldQueue" in content and (job := reader.queued_class()) is not None:
            self.jobs.setdefault(job.name, job)
        if "dispatch" in content:
            self.sites.extend(reader.dispatches())
        if "Queue::" in content:
            self.sites.extend(reader.facade_pushes())
        if "job" in content:
            self.sites.extend(reader.scheduled_jobs())

    def finish(self) -> list[Contract]:
        out: list[Contract] = []
        named: dict[str, set[str]] = defaultdict(set)
        for site in self.sites:
            if site.queue:
                queues = [site.queue]
                if site.job:
                    named[site.job].add(site.queue)
            else:
                job = self.jobs.get(site.job or "")
                queues = sorted(job.queues) if job else []
            extra = {"job": site.job} if site.job else {}
            for queue in queues:
                if queue == _DEFAULT_QUEUE:
                    continue
                out.append(self._contract(site.ctx, site.offset, queue, "provider", site.label, extra))
        for job in self.jobs.values():
            short = _short(job.name)
            for queue in sorted((job.queues | named.get(job.name, set())) - {_DEFAULT_QUEUE}):
                out.append(self._contract(job.ctx, job.offset, queue, "consumer", short, {"job": job.name}))
        return out

    @staticmethod
    def _contract(
        ctx: ScanContext, offset: int, queue: str, role: str, label: str, extra: dict[str, str]
    ) -> Contract:
        return topic_contract(
            ctx,
            name=queue,
            broker=_BROKER,
            kind=TOPIC_KIND_QUEUE,
            role=role,
            label=label,
            confidence=0.8,
            offset=offset,
            **extra,
        )


class LaravelQueueDialect:
    name = _BROKER
    extensions = PHP

    def repo_pass(self) -> _LaravelQueuePass:
        return _LaravelQueuePass()

    def extract(self, ctx: ScanContext) -> list[Contract]:
        """One file on its own (the extractor uses :meth:`repo_pass` instead)."""
        repo_pass = self.repo_pass()
        repo_pass.scan(ctx)
        return repo_pass.finish()


LARAVEL_QUEUES = LaravelQueueDialect()

__all__ = ["LARAVEL_QUEUES"]
