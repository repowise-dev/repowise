"""Ruby constant references resolve through the Zeitwerk index (``tests/fixtures/rails_app``).

Rails code names its dependencies as constants (``ConversationFinder.new``,
``::BulkActionsJob.perform_later``, ``include AutoAssignmentHandler``) and
never ``require``s them, so without these edges every service, job and
finder of a Rails app reads as unreachable and a model shows only its
``has_many`` neighbours as dependents.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from repowise.core.pipeline.incremental import build_repo_graph

FIXTURE = Path(__file__).resolve().parents[2] / "fixtures" / "rails_app"

CONVERSATIONS = "app/controllers/api/v1/accounts/conversations_controller.rb"
BULK_ACTIONS = "app/controllers/api/v1/accounts/bulk_actions_controller.rb"
FINDER = "app/finders/conversation_finder.rb"
MODEL = "app/models/conversation.rb"
CONCERN = "app/models/concerns/auto_assignment_handler.rb"
JOB = "app/jobs/bulk_actions_job.rb"


def _copy_lf(src: Path, dst: Path) -> None:
    for path in sorted(src.rglob("*")):
        if path.is_file():
            target = dst / path.relative_to(src)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(path.read_bytes().replace(b"\r\n", b"\n"))


@pytest.fixture(scope="module")
def graph(tmp_path_factory: pytest.TempPathFactory):
    root = tmp_path_factory.mktemp("rails") / "repo"
    _copy_lf(FIXTURE, root)
    _parsed, _sources, builder, _structure, _count = build_repo_graph(
        root, [], collect_sources=True, read_only=True
    )
    return builder.graph()


def _edge(graph, source: str, target: str) -> dict:
    data = graph.get_edge_data(source, target)
    assert data is not None, f"no edge {source} -> {target}"
    return data


class TestConstantReferenceEdges:
    def test_constant_receivers_link_files(self, graph) -> None:
        assert _edge(graph, CONVERSATIONS, FINDER)["edge_type"] == "type_use"
        assert _edge(graph, CONVERSATIONS, MODEL)["edge_type"] == "type_use"
        # ``::BulkActionsJob`` is top level; ``EnterpriseBilling`` lives in an
        # overlay ``lib`` that config/application.rb adds to the eager-load paths.
        assert _edge(graph, BULK_ACTIONS, JOB)["edge_type"] == "type_use"
        assert graph.has_edge(BULK_ACTIONS, "enterprise/lib/enterprise_billing.rb")

    def test_namespaced_constant_and_value_constant(self, graph) -> None:
        # ``AutoAssignment::Runner.new`` and ``Conversation::STATUSES``.
        assert graph.has_edge(CONCERN, "app/services/auto_assignment/runner.rb")
        assert graph.has_edge(FINDER, MODEL)

    def test_every_app_dir_and_concerns_are_autoload_roots(self, graph) -> None:
        # app/builders and app/listeners were missing from a fixed root list,
        # and a concern lives at the top level, not under ``Concerns::``.
        assert graph.has_edge(
            "app/listeners/conversation_listener.rb",
            "app/builders/campaign_conversation_builder.rb",
        )
        assert graph.has_edge(MODEL, CONCERN)

    def test_mixins_and_superclasses_widen_scope_other_mentions_do_not(self, graph) -> None:
        assert not _edge(graph, MODEL, CONCERN).get("no_scope_widening")
        assert not _edge(graph, MODEL, "app/models/application_record.rb").get("no_scope_widening")
        assert _edge(graph, CONVERSATIONS, FINDER).get("no_scope_widening")

    def test_definitions_and_library_constants_draw_no_edge(self, graph) -> None:
        # ``class ApplicationRecord < ActiveRecord::Base``: the name it defines
        # is no reference and ActiveRecord is no file of the repository.
        assert not any(
            data.get("edge_type") == "type_use"
            for _, _, data in graph.out_edges("app/models/application_record.rb", data=True)
        )


class TestConstantCallEdges:
    def test_new_reaches_initialize(self, graph) -> None:
        data = _edge(
            graph,
            f"{CONVERSATIONS}::Api::V1::Accounts::ConversationsController::conversation_finder",
            f"{FINDER}::ConversationFinder::initialize",
        )
        assert data["resolution_origin"] == "receiver_import"

    def test_perform_later_reaches_perform(self, graph) -> None:
        assert graph.has_edge(
            f"{BULK_ACTIONS}::Api::V1::Accounts::BulkActionsController::create",
            f"{JOB}::BulkActionsJob::perform",
        )

    def test_receiver_named_after_a_model_reaches_its_method(self, graph) -> None:
        data = _edge(
            graph,
            f"{CONVERSATIONS}::Api::V1::Accounts::ConversationsController::toggle_status",
            f"{MODEL}::Conversation::toggle_status",
        )
        assert data["resolution_origin"] == "receiver_typed_global"
        assert graph.has_edge(
            "app/services/auto_assignment/runner.rb::AutoAssignment::Runner::perform",
            f"{MODEL}::Conversation::toggle_status",
        )


def test_class_call_binds_only_the_receivers_own_method(tmp_path: Path) -> None:
    """Two classes in one file: ``Foo.new`` never reaches the other class's method."""
    root = tmp_path / "repo"
    files = {
        "config/application.rb": "module App; class Application; end; end\n",
        "app/services/report.rb": (
            "class Report\n  def build\n  end\nend\n\n"
            "class Report::Row\n  def initialize(cells)\n    @cells = cells\n  end\n\n"
            "  def perform\n  end\nend\n"
        ),
        "app/jobs/export_job.rb": (
            "class ExportJob\n  def perform(id)\n  end\nend\n\n"
            "class ExportJob::Retry\n  def perform(id)\n  end\nend\n"
        ),
        "app/controllers/reports_controller.rb": (
            "class ReportsController\n  def show\n    Report.new\n"
            "    ExportJob.perform_later(1)\n  end\nend\n"
        ),
    }
    for rel, text in files.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text)
    _parsed, _sources, builder, _structure, _count = build_repo_graph(
        root, [], collect_sources=True, read_only=True
    )
    graph = builder.graph()
    caller = "app/controllers/reports_controller.rb::ReportsController::show"
    assert not graph.has_edge(caller, "app/services/report.rb::Report::Row::initialize")
    assert graph.has_edge(caller, "app/jobs/export_job.rb::ExportJob::perform")
    assert not graph.has_edge(caller, "app/jobs/export_job.rb::ExportJob::Retry::perform")
