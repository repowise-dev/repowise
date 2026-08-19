"""The parse pool's worker count is bounded (issue #1394).

Every worker is a fresh spawned interpreter holding ~50 MB of private memory,
so sizing the pool from the host's core count made peak memory a function of
the machine rather than the repo.
"""

import pytest

from repowise.core.pipeline.phases import ingestion as ing


class TestParsePoolWorkers:
    """The cap, the floor, and the env override."""

    def test_the_cap_is_eight(self):
        """Pin the literal.

        Every other assertion here compares the helper's output to
        ``_MAX_PARSE_WORKERS``, which a revert of the constant would satisfy
        while reintroducing the 32-worker fleet this exists to prevent.
        """
        assert ing._MAX_PARSE_WORKERS == 8

    def test_cap_applies_to_host_core_count(self, monkeypatch):
        monkeypatch.delenv(ing._PARSE_WORKERS_ENV, raising=False)
        monkeypatch.setattr(ing.os, "cpu_count", lambda: 32)
        monkeypatch.setattr(ing.os, "process_cpu_count", lambda: 32, raising=False)
        assert ing.parse_pool_workers(5000) == 8

    def test_below_cap_is_left_alone(self, monkeypatch):
        monkeypatch.delenv(ing._PARSE_WORKERS_ENV, raising=False)
        monkeypatch.setattr(ing.os, "cpu_count", lambda: 4)
        monkeypatch.setattr(ing.os, "process_cpu_count", lambda: 4, raising=False)
        assert ing.parse_pool_workers(5000) == 4

    def test_never_exceeds_the_files_to_parse(self, monkeypatch):
        """A three-file update has no use for eight interpreters."""
        monkeypatch.delenv(ing._PARSE_WORKERS_ENV, raising=False)
        monkeypatch.setattr(ing.os, "cpu_count", lambda: 32)
        monkeypatch.setattr(ing.os, "process_cpu_count", lambda: 32, raising=False)
        assert ing.parse_pool_workers(3) == 3

    def test_empty_work_list_degrades_to_one(self):
        assert ing.parse_pool_workers(0) == 1
        assert ing.parse_pool_workers(-1) == 1

    def test_unknown_core_count_degrades_to_a_default(self, monkeypatch):
        monkeypatch.delenv(ing._PARSE_WORKERS_ENV, raising=False)
        monkeypatch.setattr(ing.os, "cpu_count", lambda: None)
        monkeypatch.setattr(ing.os, "process_cpu_count", lambda: None, raising=False)
        assert ing.parse_pool_workers(5000) == 4

    def test_affinity_aware_count_wins_over_host_count(self, monkeypatch):
        """A container pinned to 2 CPUs must not size its pool from the host."""
        monkeypatch.delenv(ing._PARSE_WORKERS_ENV, raising=False)
        monkeypatch.setattr(ing.os, "cpu_count", lambda: 32)
        monkeypatch.setattr(ing.os, "process_cpu_count", lambda: 2, raising=False)
        assert ing.parse_pool_workers(5000) == 2

    def test_missing_process_cpu_count_falls_back(self, monkeypatch):
        """Python 3.11/3.12 have no ``process_cpu_count``."""
        monkeypatch.delenv(ing._PARSE_WORKERS_ENV, raising=False)
        monkeypatch.delattr(ing.os, "process_cpu_count", raising=False)
        monkeypatch.setattr(ing.os, "cpu_count", lambda: 3)
        assert ing.parse_pool_workers(5000) == 3

    @pytest.mark.parametrize("value,expected", [("2", 2), ("16", 16), ("1", 1)])
    def test_env_override_wins_in_both_directions(self, monkeypatch, value, expected):
        monkeypatch.setenv(ing._PARSE_WORKERS_ENV, value)
        monkeypatch.setattr(ing.os, "cpu_count", lambda: 32)
        monkeypatch.setattr(ing.os, "process_cpu_count", lambda: 32, raising=False)
        assert ing.parse_pool_workers(5000) == expected

    def test_env_override_still_bounded_by_pending_files(self, monkeypatch):
        monkeypatch.setenv(ing._PARSE_WORKERS_ENV, "64")
        assert ing.parse_pool_workers(5) == 5

    @pytest.mark.parametrize("value", ["nonsense", "0", "-4", "3.5"])
    def test_unusable_env_value_is_ignored_not_fatal(self, monkeypatch, value):
        """A bad env value must never fail an otherwise healthy parse."""
        monkeypatch.setenv(ing._PARSE_WORKERS_ENV, value)
        monkeypatch.setattr(ing.os, "cpu_count", lambda: 32)
        monkeypatch.setattr(ing.os, "process_cpu_count", lambda: 32, raising=False)
        assert ing.parse_pool_workers(5000) == 8

    def test_unusable_env_value_says_so(self, monkeypatch):
        """The warning is the only feedback a misconfigured user gets."""
        monkeypatch.setenv(ing._PARSE_WORKERS_ENV, "-4")
        warned: list[tuple] = []
        monkeypatch.setattr(
            ing.logger, "warning", lambda event, **kw: warned.append((event, kw))
        )
        ing.parse_pool_workers(5000)
        assert warned and warned[0][0] == "invalid_parse_workers_env"
        assert warned[0][1]["value"] == "-4"

    def test_empty_env_value_means_unset(self, monkeypatch):
        """``export REPOWISE_PARSE_WORKERS=`` from an undefined shell variable
        is not a misconfiguration worth warning about."""
        monkeypatch.setenv(ing._PARSE_WORKERS_ENV, "")
        monkeypatch.setattr(ing.os, "cpu_count", lambda: 32)
        monkeypatch.setattr(ing.os, "process_cpu_count", lambda: 32, raising=False)
        warned: list[tuple] = []
        monkeypatch.setattr(
            ing.logger, "warning", lambda event, **kw: warned.append((event, kw))
        )
        assert ing.parse_pool_workers(5000) == 8
        assert not warned

    def test_affinity_unavailable_falls_back_to_host_count(self, monkeypatch):
        """``process_cpu_count`` exists but returns None when there is no mask."""
        monkeypatch.delenv(ing._PARSE_WORKERS_ENV, raising=False)
        monkeypatch.setattr(ing.os, "process_cpu_count", lambda: None, raising=False)
        monkeypatch.setattr(ing.os, "cpu_count", lambda: 3)
        assert ing.parse_pool_workers(5000) == 3


class TestBothCallSitesAreBounded:
    """The bound has to reach the executor on the init *and* resume paths.

    Grepping the source is what this guards: the two paths carried a duplicated
    ``min(os.cpu_count(), ...)`` expression and only one would have been noticed
    if a future edit reverted it.
    """

    @pytest.mark.parametrize("func", ["_run_ingestion", "reparse_for_resume"])
    def test_call_site_uses_the_shared_helper(self, func):
        import inspect

        target = getattr(ing, func)
        src = inspect.getsource(target)
        assert "parse_pool_workers(" in src, f"{func} must size its pool via the shared helper"
        assert "os.cpu_count()" not in src, f"{func} must not size its pool from the host core count"

    async def test_the_bound_reaches_the_real_executor(self, tmp_path, monkeypatch):
        """Record what ``ProcessPoolExecutor`` is really constructed with.

        The source-text assertions above cannot catch a regression spelled a
        different way; this pins the argument itself on a real ingestion run.
        """
        monkeypatch.delenv(ing._PARSE_WORKERS_ENV, raising=False)
        monkeypatch.setattr(ing.os, "cpu_count", lambda: 64)
        monkeypatch.setattr(ing.os, "process_cpu_count", lambda: 64, raising=False)

        for i in range(12):
            (tmp_path / f"mod_{i}.py").write_text(f"def f_{i}(x):\n    return x + {i}\n")

        seen: list[int | None] = []
        real = ing.ProcessPoolExecutor

        def recording(*args, **kwargs):
            seen.append(kwargs.get("max_workers"))
            # Honour the bound under test but keep the pool tiny: this is a
            # unit test, not a benchmark, and spawning eight interpreters to
            # parse twelve files would dominate the suite's runtime.
            kwargs["max_workers"] = 1
            return real(*args, **kwargs)

        monkeypatch.setattr(ing, "ProcessPoolExecutor", recording)

        parsed, *_ = await ing._run_ingestion(
            tmp_path,
            exclude_patterns=None,
            skip_tests=False,
            skip_infra=False,
            progress=None,
        )

        assert parsed, "ingestion parsed nothing, so the run proves nothing"
        assert seen, "no process pool was constructed, so the bound was never exercised"
        assert seen == [8], f"expected the capped count to reach the executor, got {seen}"
