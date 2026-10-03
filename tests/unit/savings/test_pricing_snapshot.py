"""The pricing snapshot: what it captures, and what it refuses to pay for.

The cache is not an optimization detail. Detecting the agent's model scans the
local transcripts, which was measured at about six seconds on a machine with
700 Codex sessions, and the hook is a fresh process per tool call. So "the hook
never scans" is a correctness property of the hot path, and it is asserted here
rather than trusted.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from repowise.core.savings import pricing
from repowise.core.savings.pricing import (
    PricingSnapshot,
    clear_pricing_cache,
    resolve_pricing_snapshot,
)


@pytest.fixture(autouse=True)
def _clear() -> None:
    clear_pricing_cache()
    yield
    clear_pricing_cache()


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    (tmp_path / ".repowise" / "omissions").mkdir(parents=True)
    return tmp_path


def _count_scans(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    """Replace the transcript scan with a counter, so a scan is observable."""
    calls: list[int] = []

    def fake(repo_root: Path) -> PricingSnapshot:
        calls.append(1)
        return PricingSnapshot(
            model="claude-opus-5",
            pricing_source="session_model:claude_code",
            pricing_version="pricing:test",
            input_rate_usd_per_million=5.0,
            output_rate_usd_per_million=25.0,
        )

    monkeypatch.setattr(pricing, "_scan", fake)
    return calls


def test_a_snapshot_carries_the_fields_an_event_needs(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _count_scans(monkeypatch)
    payload = resolve_pricing_snapshot(repo).as_payload()
    assert payload == {
        "model": "claude-opus-5",
        "currency": "USD",
        "pricing_source": "session_model:claude_code",
        "pricing_version": "pricing:test",
        "input_rate_usd_per_million": 5.0,
        "output_rate_usd_per_million": 25.0,
    }


def test_the_scan_runs_once_and_is_then_served_from_disk(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = _count_scans(monkeypatch)
    first = resolve_pricing_snapshot(repo)
    clear_pricing_cache()  # a fresh process, with the cache file already on disk
    second = resolve_pricing_snapshot(repo)
    assert first == second
    assert len(calls) == 1


def test_the_hook_reads_the_cache_but_never_scans(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The whole reason the switch exists."""
    calls = _count_scans(monkeypatch)

    # Nothing cached yet: the hook takes an unpriced event rather than a stall.
    assert resolve_pricing_snapshot(repo, allow_scan=False) is None
    assert calls == []

    # Once some other surface has filled the cache, the hook gets a price.
    resolve_pricing_snapshot(repo)
    clear_pricing_cache()
    assert resolve_pricing_snapshot(repo, allow_scan=False) is not None
    assert len(calls) == 1


#: Modules that call the resolver from inside an agent's own tool call. Each
#: must pass ``allow_scan=False`` at every call site, without exception.
_AGENT_FACING = (
    "repowise.server.mcp_server._savings.event",
    "repowise.cli.commands.augment_cmd._shared",
)


def test_no_agent_facing_surface_can_trigger_a_scan() -> None:
    """The surfaces that run inside a tool call must never resolve inline.

    Asserted against the call sites rather than left to the docstrings, because
    the only thing that made the regression visible was an unrelated test file
    getting fourteen times slower. The MCP path resolved inline, so the first
    tool call in every repository paid the full six-second scan, and not one
    assertion failed.
    """
    import ast
    import importlib
    import inspect

    checked = 0
    for module_name in _AGENT_FACING:
        module = importlib.import_module(module_name)
        tree = ast.parse(inspect.getsource(module))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)
            if name != "resolve_pricing_snapshot":
                continue
            checked += 1
            flags = {kw.arg: kw.value for kw in node.keywords}
            assert "allow_scan" in flags, f"{module_name} resolves without allow_scan"
            value = flags["allow_scan"]
            assert isinstance(value, ast.Constant) and value.value is False, (
                f"{module_name} must pass allow_scan=False"
            )
    # The walk finding nothing would pass vacuously, which is the failure mode
    # this whole test exists to prevent.
    assert checked == len(_AGENT_FACING)


def test_the_distill_engine_only_scans_off_the_hook_path() -> None:
    """The rewrite hook turns the agent's Bash call into ``repowise distill``.

    So the engine's surface decides: a direct CLI run may scan, a hook-sourced
    one may not. Reading the flag rather than trusting the comment, since the
    two disagreed once already.
    """
    import ast
    import inspect

    from repowise.core.distill import engine

    tree = ast.parse(inspect.getsource(engine))
    calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and getattr(node.func, "id", getattr(node.func, "attr", None))
        == "resolve_pricing_snapshot"
    ]
    assert len(calls) == 1
    flags = {kw.arg: kw.value for kw in calls[0].keywords}
    # Conditional on the surface, not an unconditional True.
    assert isinstance(flags.get("allow_scan"), ast.Compare)


def test_declining_to_scan_does_not_poison_the_memo(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A hook miss must not make the next scanning caller wait out a TTL."""
    calls = _count_scans(monkeypatch)
    assert resolve_pricing_snapshot(repo, allow_scan=False) is None
    assert resolve_pricing_snapshot(repo) is not None
    assert len(calls) == 1


def test_a_stale_cache_is_rescanned(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _count_scans(monkeypatch)
    resolve_pricing_snapshot(repo)
    cache = repo / ".repowise" / "omissions" / "pricing-snapshot.json"
    payload = json.loads(cache.read_text(encoding="utf-8"))
    payload["resolved_at"] = time.time() - (pricing._TTL_SECONDS + 60)
    cache.write_text(json.dumps(payload), encoding="utf-8")
    clear_pricing_cache()

    resolve_pricing_snapshot(repo)
    assert len(calls) == 2


@pytest.mark.parametrize(
    "content",
    [
        "not json at all",
        json.dumps({"version": 999, "resolved_at": 0}),
        json.dumps({"version": 1, "resolved_at": "yesterday", "model": "m"}),
        json.dumps({"version": 1, "resolved_at": True, "model": "m"}),
        json.dumps({"version": 1, "resolved_at": 0}),  # epoch: older than any TTL
        json.dumps({"version": 1, "resolved_at": time.time(), "model": "m" * 129}),
        json.dumps([1, 2, 3]),
    ],
)
def test_an_unusable_cache_is_treated_as_absent(
    repo: Path, monkeypatch: pytest.MonkeyPatch, content: str
) -> None:
    """Never an exception on a read path, and never a half-built snapshot."""
    calls = _count_scans(monkeypatch)
    cache = repo / ".repowise" / "omissions" / "pricing-snapshot.json"
    cache.write_text(content, encoding="utf-8")

    assert resolve_pricing_snapshot(repo) is not None
    assert len(calls) == 1
    clear_pricing_cache()
    cache.write_text(content, encoding="utf-8")
    assert resolve_pricing_snapshot(repo, allow_scan=False) is None


def test_a_missing_repository_is_unpriced_rather_than_an_error() -> None:
    assert resolve_pricing_snapshot(None) is None


def test_a_failing_scan_never_raises(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(repo_root: Path) -> PricingSnapshot:
        raise RuntimeError("transcripts exploded")

    monkeypatch.setattr(pricing, "_scan", boom)
    assert resolve_pricing_snapshot(repo) is None


def test_the_cache_write_leaves_no_temporary_behind(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _count_scans(monkeypatch)
    resolve_pricing_snapshot(repo)
    directory = repo / ".repowise" / "omissions"
    assert [p.name for p in directory.iterdir()] == ["pricing-snapshot.json"]


def test_the_pricing_table_version_changes_with_the_table(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A hand-maintained version would go stale on exactly the edit that matters.

    So the property under test is that editing a rate moves the version. Each
    table is edited separately, because a version derived from only one of them
    would still look correct here if the others were left out.
    """
    from repowise.core.generation import cost_tracker

    before = cost_tracker.pricing_table_version()
    assert before == cost_tracker.pricing_table_version()  # stable across calls
    assert before.startswith("pricing:")

    monkeypatch.setitem(cost_tracker._PRICING, "claude-sonnet-4-6", {"input": 9.0, "output": 9.0})
    assert cost_tracker.pricing_table_version() != before
    monkeypatch.undo()

    monkeypatch.setitem(cost_tracker._FALLBACK_PRICING, "input", 99.0)
    assert cost_tracker.pricing_table_version() != before
    monkeypatch.undo()

    monkeypatch.setattr(
        cost_tracker,
        "_CLAUDE_FAMILY_PRICING",
        (("claude-opus", {"input": 99.0, "output": 99.0}),),
    )
    assert cost_tracker.pricing_table_version() != before


def test_a_model_id_too_long_for_the_contract_yields_no_snapshot(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An unusable snapshot must cost the event its price, not its existence.

    The model id is read out of a transcript, and the event contract bounds it
    at 128 characters by raising -- which would drop the whole event before it
    was written, so a repository with one odd transcript would silently record
    no savings at all for a day.
    """
    from repowise.core.distill import session_model

    monkeypatch.setattr(
        session_model,
        "resolve_session_model",
        lambda *a, **k: session_model.ResolvedModel(
            model="m" * 129, raw="m" * 129, agent="claude_code", source="detected"
        ),
    )
    assert resolve_pricing_snapshot(repo) is None
    # And nothing unusable was cached for the next caller to read back.
    assert resolve_pricing_snapshot(repo, allow_scan=False) is None


def test_a_cached_snapshot_expires_when_the_file_does(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The memo must not refresh a snapshot's lifetime every time it is read.

    Otherwise a long-lived process that reads a 23-hour-old file keeps serving
    it for another full day.
    """
    calls = _count_scans(monkeypatch)
    resolve_pricing_snapshot(repo)
    cache = repo / ".repowise" / "omissions" / "pricing-snapshot.json"
    payload = json.loads(cache.read_text(encoding="utf-8"))
    payload["resolved_at"] = time.time() - (pricing._TTL_SECONDS - 5)
    cache.write_text(json.dumps(payload), encoding="utf-8")
    clear_pricing_cache()

    resolve_pricing_snapshot(repo)  # reads the nearly-expired file
    assert len(calls) == 1
    entry = pricing._memo[str(repo)]
    # Seconds of life left, not another whole TTL.
    assert entry[1] - time.monotonic() < 60


def test_a_scan_whose_cache_write_fails_is_still_used(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A six-second scan must not be thrown away because the disk said no."""
    calls = _count_scans(monkeypatch)

    def refuse(path: Path, snapshot: PricingSnapshot) -> None:
        raise OSError("read-only file system")

    monkeypatch.setattr(pricing, "_write_cache", refuse)
    assert resolve_pricing_snapshot(repo) is not None
    # And the process does not scan again for the same repository.
    assert resolve_pricing_snapshot(repo) is not None
    assert len(calls) == 1


# -- a rate the table did not know ------------------------------------------
#
# The defect these cover was live, and it was invisible precisely because it
# succeeded: ``get_model_pricing`` answers a model it does not know with the
# default tier and a warning into a log no caller reads, so a session on an
# unrecognised model was stamped $3/$15 with a real-looking
# ``session_model:<agent>`` provenance. Nothing downstream could tell that
# from a measurement.


def _resolving_to(monkeypatch: pytest.MonkeyPatch, model: str) -> None:
    """Make the session detector report *model*, whatever is on this machine."""
    from repowise.core.distill import session_model

    monkeypatch.setattr(
        session_model,
        "resolve_session_model",
        lambda *a, **k: session_model.ResolvedModel(
            model=model, raw=model, agent="claude_code", source="detected"
        ),
    )


def test_a_model_the_rate_table_does_not_know_is_left_unpriced(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _resolving_to(monkeypatch, "totally-made-up-model-xyz")

    assert resolve_pricing_snapshot(repo) is None
    # And nothing was cached, so the next caller does not read a rate back.
    assert resolve_pricing_snapshot(repo, allow_scan=False) is None


def test_the_unknown_rate_refused_is_the_fallback_that_used_to_be_stamped(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Pins *what* was being fabricated, not merely that something was.

    Without this the test above would still pass if the fallback tier happened
    to equal a real rate, which is exactly why the bug survived: $3/$15 is a
    plausible number, and a plausible number is indistinguishable from a
    measured one once it has been written down.
    """
    from repowise.core.generation import cost_tracker

    model = "totally-made-up-model-xyz"
    assert cost_tracker.resolve_model_pricing(model) is None
    # The literals, not the module constant against itself: the point is that
    # the fabrication is specifically $3/$15, a number plausible enough that
    # nobody noticed it was invented.
    assert cost_tracker.get_model_pricing(model) == {"input": 3.0, "output": 15.0}

    _resolving_to(monkeypatch, model)
    assert resolve_pricing_snapshot(repo) is None


@pytest.mark.parametrize("label", ["codex-auto-review", "<synthetic>", "auto"])
def test_a_label_that_is_not_a_model_at_all_is_left_unpriced(
    repo: Path, monkeypatch: pytest.MonkeyPatch, label: str
) -> None:
    """These need no rule of their own: they are unknown to the table, so the
    unknown-model refusal already covers them."""
    _resolving_to(monkeypatch, label)

    assert resolve_pricing_snapshot(repo) is None


def test_a_model_the_table_does_know_is_still_priced(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The confinement. Refusing the unknown must not refuse everything."""
    _resolving_to(monkeypatch, "claude-opus-5")

    snapshot = resolve_pricing_snapshot(repo)
    assert snapshot is not None
    assert snapshot.model == "claude-opus-5"
    assert snapshot.input_rate_usd_per_million == 5.0
    assert snapshot.pricing_source == "session_model:claude_code"


def test_a_family_prefix_is_a_real_rate_rather_than_a_miss(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``gpt-5.6-sol`` is in no exact row but resolves by family, and the
    family tier is a measurement. Refusing it would throw away 93% of this
    machine's ledger for nothing."""
    _resolving_to(monkeypatch, "gpt-5.6-sol")

    snapshot = resolve_pricing_snapshot(repo)
    assert snapshot is not None
    assert snapshot.input_rate_usd_per_million == 2.5


def test_a_local_model_is_priced_at_zero_rather_than_refused(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Zero is an answer, not an absence."""
    _resolving_to(monkeypatch, "ollama/llama3")

    snapshot = resolve_pricing_snapshot(repo)
    assert snapshot is not None
    assert snapshot.input_rate_usd_per_million == 0.0


@pytest.mark.parametrize(
    "model",
    [
        "anthropic.claude-sonnet-4-5-20250929-v1:0",
        "us.anthropic.claude-sonnet-4-5-v1:0",
    ],
)
def test_a_bedrock_model_is_not_mistaken_for_a_free_local_one(model: str) -> None:
    """The same defect as the fabricated rate, pointed the other way.

    Bedrock addresses hosted Anthropic models as ``vendor.name-v1:0``, and the
    bare-Ollama-tag heuristic read the trailing ``:0`` as a local tag -- so a
    Bedrock user's whole backfill would be stamped $0.00 and marked measured.
    A fabricated zero is worse than ``None``: it is indistinguishable from a
    genuine local-model zero, and it silently zeroes the priced population
    instead of moving it to the unpriced one.
    """
    from repowise.core.generation import cost_tracker

    assert cost_tracker.is_local_model(model) is False
    assert cost_tracker.resolve_model_pricing(model) is None


@pytest.mark.parametrize("model", ["llama3:8b", "qwen3.5:4b", "mistral:latest", "ollama/llama3"])
def test_a_real_local_model_is_still_free(model: str) -> None:
    """The confinement. Ollama size tags are never purely numeric."""
    from repowise.core.generation import cost_tracker

    assert cost_tracker.is_local_model(model) is True
    assert cost_tracker.resolve_model_pricing(model) == {"input": 0.0, "output": 0.0}
