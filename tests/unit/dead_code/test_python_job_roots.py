"""Celery task modules and Airflow DAG files are loaded by convention, not imported.

Built through the real traverse -> parse -> graph -> framework-edges path, so
the test covers the whole chain: the handler marks the module a root and the
reachability pass reads it.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from repowise.core.analysis.dead_code import DeadCodeAnalyzer
from repowise.core.ingestion import ASTParser, FileTraverser, GraphBuilder

_CELERY_APP = (
    "from celery import Celery\n\n"
    'app = Celery("proj", include=["proj.reports"])\n'
    'app.autodiscover_tasks(["proj"])\n'
)
_TASK = "from celery import shared_task\n\n@shared_task\ndef run():\n    return 1\n"

_CELERY_LAYOUT = {
    "proj/__init__.py": "from .celery_app import app\n",
    "proj/celery_app.py": _CELERY_APP,
    "proj/tasks.py": _TASK,
    "proj/reports.py": _TASK,
}

_DAG = (
    "from airflow import DAG\n\n"
    'with DAG("nightly_etl") as dag:\n    pass\n'
)


def _unreachable(repo: Path, files: dict[str, str]) -> set[str]:
    for rel, text in files.items():
        path = repo / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    builder = GraphBuilder(repo)
    parser = ASTParser()
    source_map: dict[str, bytes] = {}
    for fi in FileTraverser(repo).traverse():
        src = Path(fi.abs_path).read_bytes()
        builder.add_file(parser.parse_file(fi, src))
        source_map[fi.path] = src
    builder.set_source_map(source_map)
    builder.build()
    builder.add_framework_edges([])
    report = DeadCodeAnalyzer(
        builder.graph(),
        {},
        parsed_files=builder._parsed_files,
        source_map=source_map,
        repo_root=repo,
    ).analyze({"detect_unused_internals": False})
    return {f.file_path for f in report.findings if f.kind.value == "unreachable_file"}


def test_celery_autodiscover_and_airflow_files_are_not_unreachable(tmp_path: Path) -> None:
    files = {**_CELERY_LAYOUT, "dags/nightly_etl.py": _DAG}
    flagged = _unreachable(tmp_path, files)
    assert "proj/tasks.py" not in flagged
    assert "dags/nightly_etl.py" not in flagged


def test_a_tasks_module_with_no_celery_app_is_still_reported(tmp_path: Path) -> None:
    flagged = _unreachable(tmp_path, {"proj/tasks.py": _TASK})
    assert "proj/tasks.py" in flagged


def test_a_module_that_only_imports_airflow_is_still_reported(tmp_path: Path) -> None:
    helper = "from airflow.models import Variable\n\ndef limit():\n    return Variable.get('n')\n"
    flagged = _unreachable(tmp_path, {"dags/helpers.py": helper})
    assert "dags/helpers.py" in flagged


def test_a_dag_without_an_airflow_import_is_still_reported(tmp_path: Path) -> None:
    flagged = _unreachable(tmp_path, {"dags/other.py": 'class DAG:\n    pass\n\nx = DAG("n")\n'})
    assert "dags/other.py" in flagged


def test_the_dag_decorator_marks_a_file(tmp_path: Path) -> None:
    text = "from airflow.decorators import dag\n\n@dag(schedule=None)\ndef etl():\n    pass\n"
    assert "dags/etl.py" not in _unreachable(tmp_path, {"dags/etl.py": text})


@pytest.mark.parametrize(
    "call",
    [
        "app.autodiscover_tasks()",
        "app.autodiscover_tasks(lambda: settings.INSTALLED_APPS)",
        "app.autodiscover_tasks(settings.INSTALLED_APPS)",
    ],
)
def test_an_unreadable_autodiscover_call_marks_every_module_that_defines_a_task(
    tmp_path: Path, call: str
) -> None:
    files = {
        "proj/celery_app.py": f"from celery import Celery\n\napp = Celery('proj')\n{call}\n",
        "proj/tasks.py": _TASK,
        "other/tasks.py": _TASK,
        "util/tasks.py": "def helper():\n    return 1\n",
    }
    flagged = _unreachable(tmp_path, files)
    assert "proj/tasks.py" not in flagged
    assert "other/tasks.py" not in flagged
    # No task is defined here, so nothing says the loader reaches it.
    assert "util/tasks.py" in flagged


def test_a_named_package_marks_only_its_own_tasks_module(tmp_path: Path) -> None:
    files = {
        "proj/celery_app.py": _CELERY_APP.replace('["proj"]', '["billing"]'),
        "billing/tasks.py": _TASK,
        "proj/tasks.py": _TASK,
    }
    flagged = _unreachable(tmp_path, files)
    assert "billing/tasks.py" not in flagged
    assert "proj/tasks.py" in flagged


def test_related_name_picks_the_module_the_loader_imports(tmp_path: Path) -> None:
    files = {
        "proj/celery_app.py": _CELERY_APP.replace(
            'autodiscover_tasks(["proj"])', 'autodiscover_tasks(["proj"], related_name="jobs")'
        ),
        "proj/jobs.py": _TASK,
        "proj/tasks.py": _TASK,
    }
    flagged = _unreachable(tmp_path, files)
    assert "proj/jobs.py" not in flagged
    assert "proj/tasks.py" in flagged
