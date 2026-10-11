"""Fixture for the performance pass (io_in_loop / string_concat / blocking_sync).

Hand-counted expectations live in ``test_perf_io_in_loop.py``. Every POSITIVE is
a genuine per-iteration I/O boundary; every NEGATIVE is a shape the loop-body
scoping / constant-loop skip / execution-sink gate must NOT flag.
"""

import os
import subprocess
import time

import httpx
import requests
from sqlalchemy import select


# --- POSITIVES -------------------------------------------------------------
def db_execute_in_loop(session, repos):
    for repo in repos:  # data-dependent loop
        session.execute(select(repo))  # POSITIVE: db sink per iteration


def subprocess_in_loop(paths):
    for p in paths:
        subprocess.run(["git", "status"], cwd=p)  # POSITIVE: subprocess


def http_in_loop(urls):
    for u in urls:
        requests.get(u)  # POSITIVE: network


async def await_http_in_loop(client, urls):
    for u in urls:
        await client.get(u)  # NEGATIVE: client root unresolved -> no false fire


def string_concat_in_loop(rows):
    out = ""
    for r in rows:
        out += "line\n"  # POSITIVE: string concat
    return out


async def blocking_in_async(urls):
    time.sleep(1)  # POSITIVE: blocking sleep in async
    requests.get(urls[0])  # POSITIVE: blocking sync http in async


# --- NEGATIVES -------------------------------------------------------------
def sink_in_loop_header(session, q):
    for row in session.execute(q).scalars().all():  # NEGATIVE: header runs once
        process(row)


def constant_range(paths):
    for _ in range(3):  # NEGATIVE: constant-bounded loop
        subprocess.run(["ls"])


def constant_literal(client):
    for url in ("a", "b"):  # NEGATIVE: literal collection
        client.get(url)


def query_builder_in_loop(repos):
    for repo in repos:
        select(repo).where(repo.id == 1)  # NEGATIVE: builder, not executed


def pure_call_in_loop(items):
    total = 0
    for x in items:
        total += compute(x)  # NEGATIVE: numeric += / pure call
    return total


def sink_outside_loop(session, q):
    result = session.execute(q)  # NEGATIVE: runs once, not in a loop
    return result


def open_in_loop(paths):
    for p in paths:
        open(p).read()  # POSITIVE: filesystem (bare open)
