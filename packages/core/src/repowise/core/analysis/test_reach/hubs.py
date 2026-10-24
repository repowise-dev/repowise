"""Hub files: modules so widely imported that reaching a file through one says little."""

from __future__ import annotations

from collections.abc import Collection, Mapping

from repowise.core.analysis.execution_graph import file_of_symbol

# A hub is a module most of the code imports: fan-in above the absolute bar, or
# in the top 1% of the repository's files and at least the floor. Far stricter
# than ``PerfRanker``'s top-quintile "central": that marks a function worth a
# performance claim, while a hub is a module most tests would pass through.
# Fan-in counts every importer, tests included, as ``graph_metrics.in_degree``
# stores it.
HUB_MIN_FAN_IN = 50
HUB_TOP_PERCENTILE = 0.99
HUB_FAN_IN_FLOOR = 10


def hub_files(in_degree: Mapping[str, float], test_files: Collection[str]) -> frozenset[str]:
    """The repository's own non-test files that are hubs by stored fan-in."""
    # Imported here: the health package imports this one, so a module-level
    # import would be a cycle.
    from repowise.core.analysis.health.perf.ranking import _percentile_threshold

    own = {
        path: int(fan_in or 0)
        for path, fan_in in in_degree.items()
        if not path.startswith("external:") and path not in test_files
    }
    bar = max(HUB_FAN_IN_FLOOR, _percentile_threshold(list(own.values()), HUB_TOP_PERCENTILE))
    return frozenset(
        path for path, fan_in in own.items() if fan_in > HUB_MIN_FAN_IN or fan_in >= bar
    )


def call_hub_files(
    call_in_degree: Mapping[str, float], test_files: Collection[str]
) -> frozenset[str]:
    """Files declaring a symbol whose call fan-in clears the hub bar.

    *call_in_degree* is each symbol's count of distinct reliable callers, tests
    included (``ExecutionGraphIndex.in_degree``). A call walk passes through
    symbols, so this is the hub that matters to it: an entry point most tests
    call reaches deep into the code for all of them.
    """
    own = {s: n for s, n in call_in_degree.items() if file_of_symbol(s) not in test_files}
    return frozenset(file_of_symbol(s) for s in hub_files(own, test_files))
