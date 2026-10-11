"""Laravel queued jobs: dispatch sites, class-declared queues, and the join between them."""

from __future__ import annotations

from pathlib import Path

import pytest

from repowise.core.workspace.extractors.topic import TopicExtractor


def _extract(tmp_path: Path, files: dict[str, str]):
    for rel, content in files.items():
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    return TopicExtractor().extract(tmp_path, "api")


def _rows(contracts) -> set[tuple[str, str, str]]:
    return {(c.role, c.contract_id, c.file_path) for c in contracts}


def _job(name: str, body: str = "", namespace: str = "App\\Jobs") -> str:
    return (
        f"<?php\n\nnamespace {namespace};\n\n"
        "use Illuminate\\Contracts\\Queue\\ShouldQueue;\n\n"
        f"class {name} implements ShouldQueue\n{{\n{body}\n    public function handle(): void {{}}\n}}\n"
    )


def _controller(body: str, uses: str = "use App\\Jobs\\SendReceipt;\n") -> str:
    return (
        "<?php\n\nnamespace App\\Http\\Controllers;\n\n"
        f"{uses}\nclass OrderController\n{{\n    public function store()\n    {{\n        {body}\n    }}\n}}\n"
    )


_JOB = "app/Jobs/SendReceipt.php"
_CTRL = "app/Http/Controllers/OrderController.php"


class TestDispatchSites:
    def test_on_queue_names_the_queue_at_both_ends(self, tmp_path: Path) -> None:
        rows = _extract(
            tmp_path,
            {
                _JOB: _job("SendReceipt"),
                _CTRL: _controller("SendReceipt::dispatch($order)->onQueue('receipts');"),
            },
        )
        assert _rows(rows) == {
            ("provider", "topic::receipts", _CTRL),
            ("consumer", "topic::receipts", _JOB),
        }
        provider = next(c for c in rows if c.role == "provider")
        assert provider.meta == {
            "topic": "receipts",
            "broker": "laravel",
            "kind": "queue",
            "job": "App\\Jobs\\SendReceipt",
        }
        assert provider.symbol_name == "SendReceipt::dispatch('receipts')"

    @pytest.mark.parametrize(
        "body",
        [
            "dispatch((new SendReceipt($order))->onQueue('receipts'));",
            "dispatch(new SendReceipt($order))->onQueue('receipts');",
            "Bus::dispatch((new SendReceipt($order))->onQueue('receipts'));",
            "Queue::pushOn('receipts', new SendReceipt($order));",
            "Queue::push(new SendReceipt($order), '', 'receipts');",
            "Queue::later(60, new SendReceipt($order), '', 'receipts');",
            "$schedule->job(new SendReceipt, 'receipts')->daily();",
        ],
    )
    def test_every_dispatch_spelling(self, tmp_path: Path, body: str) -> None:
        rows = _extract(tmp_path, {_JOB: _job("SendReceipt"), _CTRL: _controller(body)})
        assert ("provider", "topic::receipts", _CTRL) in _rows(rows)
        assert ("consumer", "topic::receipts", _JOB) in _rows(rows)

    def test_a_queue_constant_folds(self, tmp_path: Path) -> None:
        body = "SendReceipt::dispatch($o)->onQueue(self::QUEUE);\n    }\n    const QUEUE = 'receipts';\n    function x() {"
        rows = _extract(tmp_path, {_CTRL: _controller(body)})
        assert _rows(rows) == {("provider", "topic::receipts", _CTRL)}

    def test_the_default_queue_is_not_recorded(self, tmp_path: Path) -> None:
        rows = _extract(tmp_path, {_JOB: _job("SendReceipt"), _CTRL: _controller("SendReceipt::dispatch($o);")})
        assert rows == []

    def test_an_unsettled_queue_is_refused_not_replaced(self, tmp_path: Path) -> None:
        rows = _extract(
            tmp_path,
            {
                _JOB: _job("SendReceipt", "    public $queue = 'receipts';"),
                _CTRL: _controller("SendReceipt::dispatch($o)->onQueue($this->queueName());"),
            },
        )
        # The class still runs on its own queue; the site named another it did not say.
        assert _rows(rows) == {("consumer", "topic::receipts", _JOB)}

    @pytest.mark.parametrize(
        "body",
        [
            "$this->dispatch('order-saved');",  # a Livewire browser event
            "Event::dispatch(new OrderShipped($o));",
            "event(new OrderShipped($o));",
            "$this->redispatch($o);",
        ],
    )
    def test_events_are_not_jobs(self, tmp_path: Path, body: str) -> None:
        assert _extract(tmp_path, {_CTRL: _controller(body)}) == []


class TestClassDeclaredQueues:
    @pytest.mark.parametrize(
        "body",
        [
            "    public $queue = 'receipts';",
            "    public ?string $queue = 'receipts';",
            "    public function __construct() { $this->onQueue('receipts'); }",
            "    public function viaQueue(): string { return 'receipts'; }",
        ],
    )
    def test_a_class_declares_its_queue(self, tmp_path: Path, body: str) -> None:
        rows = _extract(tmp_path, {_JOB: _job("SendReceipt", body)})
        assert _rows(rows) == {("consumer", "topic::receipts", _JOB)}

    def test_a_site_naming_no_queue_takes_the_class_queue(self, tmp_path: Path) -> None:
        rows = _extract(
            tmp_path,
            {
                _JOB: _job("SendReceipt", "    public $queue = 'receipts';"),
                _CTRL: _controller("SendReceipt::dispatch($o);"),
            },
        )
        assert _rows(rows) == {
            ("provider", "topic::receipts", _CTRL),
            ("consumer", "topic::receipts", _JOB),
        }

    def test_a_site_override_adds_a_queue_the_class_runs_on(self, tmp_path: Path) -> None:
        rows = _extract(
            tmp_path,
            {
                _JOB: _job("SendReceipt", "    public $queue = 'receipts';"),
                _CTRL: _controller("SendReceipt::dispatch($o)->onQueue('urgent');"),
            },
        )
        assert {(c.role, c.contract_id) for c in rows} == {
            ("provider", "topic::urgent"),
            ("consumer", "topic::receipts"),
            ("consumer", "topic::urgent"),
        }

    def test_a_class_that_is_not_queued_consumes_nothing(self, tmp_path: Path) -> None:
        src = "<?php\nnamespace App\\Jobs;\nclass SendReceipt\n{\n    public $queue = 'receipts';\n}\n"
        assert _extract(tmp_path, {_JOB: src}) == []


class TestRefusals:
    @pytest.mark.parametrize(
        "body",
        ["SendReceipt::dispatch($o)->onQueue('default');", "SendReceipt::dispatchSync($o);"],
    )
    def test_the_default_queue_and_a_sync_dispatch_name_no_queue(
        self, tmp_path: Path, body: str
    ) -> None:
        rows = _extract(tmp_path, {_JOB: _job("SendReceipt"), _CTRL: _controller(body)})
        assert rows == []

    def test_a_class_declaring_the_default_queue_consumes_nothing(self, tmp_path: Path) -> None:
        assert _extract(tmp_path, {_JOB: _job("SendReceipt", "    public $queue = 'default';")}) == []

    def test_php_method_names_are_case_insensitive(self, tmp_path: Path) -> None:
        rows = _extract(tmp_path, {_CTRL: _controller("SendReceipt::dispatch($o)->ONQUEUE('receipts');")})
        assert _rows(rows) == {("provider", "topic::receipts", _CTRL)}

    def test_the_word_class_in_a_docblock_is_not_the_job(self, tmp_path: Path) -> None:
        src = _job("SendReceipt", "    public $queue = 'receipts';").replace(
            "class SendReceipt", "/**\n * This class sends mail.\n */\nclass SendReceipt"
        )
        rows = _extract(tmp_path, {_JOB: src, _CTRL: _controller("SendReceipt::dispatch($o);")})
        assert {c.meta["job"] for c in rows} == {"App\\Jobs\\SendReceipt"}
        assert ("provider", "topic::receipts", _CTRL) in _rows(rows)


class TestRepoPass:
    def test_two_passes_share_nothing(self) -> None:
        from repowise.core.workspace.extractors.base import ScanContext
        from repowise.core.workspace.extractors.topic.laravel import LARAVEL_QUEUES

        a, b = LARAVEL_QUEUES.repo_pass(), LARAVEL_QUEUES.repo_pass()
        a.scan(ScanContext("a", _JOB, ".php", _job("SendReceipt", "    public $queue = 'a-q';")))
        b.scan(ScanContext("b", _CTRL, ".php", _controller("SendReceipt::dispatch($o);")))
        a.scan(ScanContext("a", _CTRL, ".php", _controller("SendReceipt::dispatch($o);")))
        assert b.finish() == []
        assert {(c.repo, c.role, c.contract_id) for c in a.finish()} == {
            ("a", "provider", "topic::a-q"),
            ("a", "consumer", "topic::a-q"),
        }

    def test_one_extractor_reused_across_repos(self, tmp_path: Path) -> None:
        from repowise.core.workspace.extractors.topic import TopicExtractor

        extractor = TopicExtractor()
        for alias, rel, src in (
            ("worker", _JOB, _job("SendReceipt", "    public $queue = 'receipts';")),
            ("web", _CTRL, _controller("SendReceipt::dispatch($o);")),
        ):
            (tmp_path / alias / rel).parent.mkdir(parents=True, exist_ok=True)
            (tmp_path / alias / rel).write_text(src, encoding="utf-8")
        assert len(extractor.extract(tmp_path / "worker", "worker")) == 1
        # The class that names the queue is in the other repo: nothing joins.
        assert extractor.extract(tmp_path / "web", "web") == []


class TestNameResolution:
    def test_the_join_follows_use_aliases(self, tmp_path: Path) -> None:
        rows = _extract(
            tmp_path,
            {
                _JOB: _job("SendReceipt", "    public $queue = 'receipts';"),
                _CTRL: _controller(
                    "Receipt::dispatch($o);", uses="use App\\Jobs\\SendReceipt as Receipt;\n"
                ),
            },
        )
        assert ("provider", "topic::receipts", _CTRL) in _rows(rows)

    def test_a_same_short_name_in_another_namespace_is_not_joined(self, tmp_path: Path) -> None:
        other = "app/Billing/SendReceipt.php"
        rows = _extract(
            tmp_path,
            {
                other: _job("SendReceipt", "    public $queue = 'billing';", "App\\Billing"),
                _CTRL: _controller("SendReceipt::dispatch($o);"),
            },
        )
        assert _rows(rows) == {("consumer", "topic::billing", other)}

    def test_a_same_namespace_class_needs_no_use(self, tmp_path: Path) -> None:
        dispatcher = "app/Jobs/Fanout.php"
        src = "<?php\nnamespace App\\Jobs;\nclass Fanout\n{\n    function run() { SendReceipt::dispatch(); }\n}\n"
        rows = _extract(
            tmp_path,
            {_JOB: _job("SendReceipt", "    public $queue = 'receipts';"), dispatcher: src},
        )
        assert ("provider", "topic::receipts", dispatcher) in _rows(rows)
