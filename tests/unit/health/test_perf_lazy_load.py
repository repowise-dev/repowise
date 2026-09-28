"""Unit tests for ``lazy_load_in_loop``.

Multi-file fixtures: models live in one file, the loop in another, mirroring
the cross-file model index the detector needs. ``parsed_files`` always lists
every fixture file; ``walked`` lists only the file being scored (as on an
incremental update).
"""

from __future__ import annotations

import textwrap
from dataclasses import dataclass
from pathlib import Path

import pytest

from repowise.core.analysis.health.complexity import FileComplexity
from repowise.core.analysis.health.perf.lazy_load import collect_lazy_loads
from repowise.core.analysis.health.perf.orm_models import build_model_index

_KIND = "lazy_load_in_loop"


def _require_python() -> None:
    try:
        from repowise.core.ingestion.parser import _get_language
    except Exception:
        pytest.skip("tree-sitter language pack missing for python")
    if _get_language("python") is None:
        pytest.skip("tree-sitter language pack missing for python")


@dataclass
class _FileInfo:
    path: str
    abs_path: str
    language: str


@dataclass
class _ParsedFile:
    file_info: _FileInfo


def _write(tmp_path: Path, name: str, src: str) -> _ParsedFile:
    p = tmp_path / name
    p.write_text(textwrap.dedent(src), encoding="utf-8")
    return _ParsedFile(_FileInfo(path=name, abs_path=str(p), language="python"))


def _hits(tmp_path: Path, models_src: str, loop_src: str) -> list:
    _require_python()
    models_pf = _write(tmp_path, "models.py", models_src)
    loop_pf = _write(tmp_path, "loop.py", loop_src)
    fcx = FileComplexity(functions=[], classes=[])
    collect_lazy_loads([(loop_pf, fcx)], [models_pf, loop_pf])
    return [h for h in fcx.perf_hits if h.kind == _KIND]


# -- SQLAlchemy positives ---------------------------------------------------


def test_sa_query_filter_all(tmp_path: Path):
    hits = _hits(
        tmp_path,
        """
        class Incident:
            owner = relationship("User")
        """,
        """
        import sqlite3

        def f(session):
            for i in session.query(Incident).filter(Incident.id > 1).all():
                print(i.owner.name)
        """,
    )
    assert len(hits) == 1
    assert hits[0].function == "f"
    assert hits[0].detail == "db"
    assert hits[0].path == ("Incident.owner", "selectinload(Incident.owner)")
    assert hits[0].loop is not None
    assert hits[0].loop.magnitude == "grows_with_data"


def test_sa_flask_query_filter_by(tmp_path: Path):
    hits = _hits(
        tmp_path,
        """
        class Users:
            profile = relationship("Profile")
        """,
        """
        def f():
            for u in Users.query.filter_by(active=True):
                print(u.profile.bio)
        """,
    )
    assert len(hits) == 1
    assert hits[0].path == ("Users.profile", "selectinload(Users.profile)")


def test_sa_select_via_scalars(tmp_path: Path):
    hits = _hits(
        tmp_path,
        """
        class Build:
            repo = relationship("Repo")
        """,
        """
        def f(session):
            stmt = select(Build).where(Build.id > 1)
            for b in session.scalars(stmt):
                print(b.repo.name)
        """,
    )
    assert len(hits) == 1
    assert hits[0].path == ("Build.repo", "selectinload(Build.repo)")


def test_sa_execute_scalars(tmp_path: Path):
    hits = _hits(
        tmp_path,
        """
        class Build:
            repo = relationship("Repo")
        """,
        """
        def f(session):
            for b in session.execute(select(Build)).scalars():
                print(b.repo.name)
        """,
    )
    assert len(hits) == 1


def test_sa_mapped_relationship(tmp_path: Path):
    hits = _hits(
        tmp_path,
        """
        class Team:
            members: Mapped[list["Member"]] = relationship()
        """,
        """
        def f(session):
            for t in session.query(Team).all():
                print(t.members)
        """,
    )
    assert len(hits) == 1
    assert hits[0].path == ("Team.members", "selectinload(Team.members)")


def test_sa_declared_attr_on_mixin(tmp_path: Path):
    hits = _hits(
        tmp_path,
        """
        class OwnerMixin:
            @declared_attr
            def owner(cls):
                return relationship("User")

        class Incident(OwnerMixin, Base):
            pass
        """,
        """
        def f(session):
            for i in session.query(Incident).all():
                print(i.owner.name)
        """,
    )
    assert len(hits) == 1
    assert hits[0].path == ("Incident.owner", "selectinload(Incident.owner)")


def test_sa_backref(tmp_path: Path):
    hits = _hits(
        tmp_path,
        """
        class Parent:
            children = relationship("Child", backref="parent")

        class Child:
            pass
        """,
        """
        def f(session):
            for c in session.query(Child).all():
                print(c.parent.name)
        """,
    )
    assert len(hits) == 1
    assert hits[0].path == ("Child.parent", "selectinload(Child.parent)")


def test_sa_lazy_dynamic(tmp_path: Path):
    hits = _hits(
        tmp_path,
        """
        class Team:
            members = relationship("Member", lazy="dynamic")
        """,
        """
        def f(session):
            for t in session.query(Team).all():
                print(t.members)
        """,
    )
    assert len(hits) == 1


def test_sa_list_comprehension(tmp_path: Path):
    hits = _hits(
        tmp_path,
        """
        class Incident:
            owner = relationship("User")
        """,
        """
        def f(session):
            return [i.owner.name for i in session.query(Incident).all()]
        """,
    )
    assert len(hits) == 1
    assert hits[0].loop is None


def test_sa_enumerate(tmp_path: Path):
    hits = _hits(
        tmp_path,
        """
        class Incident:
            owner = relationship("User")
        """,
        """
        def f(session):
            for idx, i in enumerate(session.query(Incident).all()):
                print(idx, i.owner.name)
        """,
    )
    assert len(hits) == 1


# -- SQLAlchemy negatives ----------------------------------------------------


@pytest.mark.parametrize("mode", ["joined", "selectin", "raise"])
def test_sa_eager_lazy_mode_no_hit(tmp_path: Path, mode: str):
    hits = _hits(
        tmp_path,
        f"""
        class Incident:
            owner = relationship("User", lazy="{mode}")
        """,
        """
        def f(session):
            for i in session.query(Incident).all():
                print(i.owner.name)
        """,
    )
    assert hits == []


def test_sa_options_joinedload_on_producer(tmp_path: Path):
    hits = _hits(
        tmp_path,
        """
        class Incident:
            owner = relationship("User")
        """,
        """
        def f(session):
            for i in session.query(Incident).options(joinedload(Incident.owner)).all():
                print(i.owner.name)
        """,
    )
    assert hits == []


def test_sa_options_selectinload_on_refinement(tmp_path: Path):
    hits = _hits(
        tmp_path,
        """
        class Incident:
            owner = relationship("User")
        """,
        """
        def f(session):
            qs = session.query(Incident)
            qs = qs.options(selectinload("owner"))
            for i in qs.all():
                print(i.owner.name)
        """,
    )
    assert hits == []


def test_sa_options_on_statement_behind_scalars(tmp_path: Path):
    hits = _hits(
        tmp_path,
        """
        class Incident:
            owner = relationship("User")
        """,
        """
        def f(session):
            stmt = select(Incident).options(joinedload(Incident.owner))
            rows = session.scalars(stmt).all()
            for i in rows:
                print(i.owner.name)
        """,
    )
    assert hits == []


def test_sa_column_named_like_relationship_elsewhere(tmp_path: Path):
    hits = _hits(
        tmp_path,
        """
        class Incident:
            pass

        class Event:
            owner = relationship("User")
        """,
        """
        def f(session):
            for e in session.query(Incident).all():
                print(e.owner)
        """,
    )
    assert hits == []


def test_sa_with_entities_no_hit(tmp_path: Path):
    hits = _hits(
        tmp_path,
        """
        class Incident:
            owner = relationship("User")
        """,
        """
        def f(session):
            for i in session.query(Incident).with_entities(Incident.id).all():
                print(i.owner)
        """,
    )
    assert hits == []


def test_sa_rows_as_parameter_no_hit(tmp_path: Path):
    hits = _hits(
        tmp_path,
        """
        class Incident:
            owner = relationship("User")
        """,
        """
        def f(rows):
            for i in rows:
                print(i.owner.name)
        """,
    )
    assert hits == []


def test_sa_async_def_no_hit(tmp_path: Path):
    hits = _hits(
        tmp_path,
        """
        class Incident:
            owner = relationship("User")
        """,
        """
        async def f(session):
            for i in session.query(Incident).all():
                print(i.owner.name)
        """,
    )
    assert hits == []


def test_sa_constant_loop_no_hit(tmp_path: Path):
    hits = _hits(
        tmp_path,
        """
        class Incident:
            owner = relationship("User")
        """,
        """
        def f(session):
            incidents = session.query(Incident).all()
            for i in [incidents]:
                print(i.owner)
        """,
    )
    assert hits == []


def test_sa_write_target_no_hit(tmp_path: Path):
    hits = _hits(
        tmp_path,
        """
        class Incident:
            owner = relationship("User")
        """,
        """
        def f(session, x):
            for i in session.query(Incident).all():
                i.owner = x
        """,
    )
    assert hits == []


def test_model_index_name_collision_intersection(tmp_path: Path):
    """Two different ``Incident`` classes across files keep only the attrs both
    define with the same ``lazy`` value."""
    _require_python()
    a = _write(
        tmp_path,
        "a.py",
        """
        class Incident:
            owner = relationship("User")
            team = relationship("Team")
        """,
    )
    b = _write(
        tmp_path,
        "b.py",
        """
        class Incident:
            owner = relationship("User")
            team = relationship("Team", lazy="joined")
        """,
    )
    index = build_model_index([a, b], lambda p: Path(p).read_bytes())
    assert set(index.relations["Incident"]) == {"owner"}


# -- Django positives ---------------------------------------------------------


def test_django_objects_filter(tmp_path: Path):
    hits = _hits(
        tmp_path,
        """
        # django
        class Issue(models.Model):
            project = models.ForeignKey(Project)
        """,
        """
        import sqlite3

        def f():
            for i in Issue.objects.filter(open=True).all():
                print(i.project.name)
        """,
    )
    assert len(hits) == 1
    assert hits[0].path == ("Issue.project", 'select_related("project")')
    assert hits[0].loop.magnitude == "grows_with_data"


def test_django_reverse_default_related_name(tmp_path: Path):
    hits = _hits(
        tmp_path,
        """
        # django
        class Issue(models.Model):
            project = models.ForeignKey(Project)

        class Project(models.Model):
            pass
        """,
        """
        import sqlite3

        def f():
            for p in Project.objects.all():
                print(p.issue_set.count())
        """,
    )
    assert len(hits) == 1
    assert hits[0].path == ("Project.issue_set", 'prefetch_related("issue_set")')


def test_django_related_name(tmp_path: Path):
    hits = _hits(
        tmp_path,
        """
        # django
        class Issue(models.Model):
            project = models.ForeignKey(Project, related_name="issues")

        class Project(models.Model):
            pass
        """,
        """
        def f():
            for p in Project.objects.all():
                print(p.issues.count())
        """,
    )
    assert len(hits) == 1
    assert hits[0].path == ("Project.issues", 'prefetch_related("issues")')


def test_django_m2m_all(tmp_path: Path):
    hits = _hits(
        tmp_path,
        """
        # django
        class Issue(models.Model):
            tags = models.ManyToManyField(Tag)
        """,
        """
        def f():
            for i in Issue.objects.all():
                print(i.tags.all())
        """,
    )
    assert len(hits) == 1
    assert hits[0].path == ("Issue.tags", 'prefetch_related("tags")')


def test_django_refinement(tmp_path: Path):
    hits = _hits(
        tmp_path,
        """
        # django
        class Issue(models.Model):
            project = models.ForeignKey(Project)
        """,
        """
        def f():
            qs = Issue.objects.filter(open=True)
            qs = qs.filter(assigned=True)
            for i in qs:
                print(i.project.name)
        """,
    )
    assert len(hits) == 1


# -- Django negatives ----------------------------------------------------------


def test_django_select_related_no_args(tmp_path: Path):
    hits = _hits(
        tmp_path,
        """
        # django
        class Issue(models.Model):
            project = models.ForeignKey(Project)
        """,
        """
        def f():
            for i in Issue.objects.select_related().all():
                print(i.project.name)
        """,
    )
    assert hits == []


def test_django_select_related_named(tmp_path: Path):
    hits = _hits(
        tmp_path,
        """
        # django
        class Issue(models.Model):
            project = models.ForeignKey(Project)
        """,
        """
        def f():
            for i in Issue.objects.select_related("project"):
                print(i.project.name)
        """,
    )
    assert hits == []


def test_django_prefetch_related_prefetch_object(tmp_path: Path):
    hits = _hits(
        tmp_path,
        """
        # django
        class Issue(models.Model):
            tags = models.ManyToManyField(Tag)
        """,
        """
        def f():
            for i in Issue.objects.prefetch_related(Prefetch("tags")):
                print(i.tags.all())
        """,
    )
    assert hits == []


def test_django_values_no_hit(tmp_path: Path):
    hits = _hits(
        tmp_path,
        """
        # django
        class Issue(models.Model):
            project = models.ForeignKey(Project)
        """,
        """
        def f():
            for i in Issue.objects.values("id"):
                print(i.project)
        """,
    )
    assert hits == []


def test_django_fk_id_no_hit(tmp_path: Path):
    hits = _hits(
        tmp_path,
        """
        # django
        class Issue(models.Model):
            project = models.ForeignKey(Project)
        """,
        """
        def f():
            for i in Issue.objects.all():
                print(i.project_id)
        """,
    )
    assert hits == []


def test_django_related_name_plus_no_reverse(tmp_path: Path):
    hits = _hits(
        tmp_path,
        """
        # django
        class Issue(models.Model):
            project = models.ForeignKey(Project, related_name="+")

        class Project(models.Model):
            pass
        """,
        """
        def f():
            for p in Project.objects.all():
                print(p.issue_set.count())
        """,
    )
    assert hits == []


# -- shape --------------------------------------------------------------------


def test_one_hit_per_loop_with_two_lazy_accesses(tmp_path: Path):
    hits = _hits(
        tmp_path,
        """
        class Incident:
            owner = relationship("User")
            team = relationship("Team")
        """,
        """
        def f(session):
            for i in session.query(Incident).all():
                print(i.owner.name, i.team.name)
        """,
    )
    assert len(hits) == 1


# -- model index: byte prefilter ----------------------------------------------


def test_model_index_only_built_from_prefiltered_files(tmp_path: Path):
    _require_python()
    with_orm = _write(
        tmp_path,
        "with_orm.py",
        """
        class Incident:
            owner = relationship("User")
        """,
    )
    without_orm = _write(
        tmp_path,
        "without_orm.py",
        """
        class Plain:
            name = "x"
        """,
    )
    index = build_model_index([with_orm, without_orm], lambda p: Path(p).read_bytes())
    assert "Incident" in index.models
    assert "Plain" not in index.models


def test_django_manager_write_no_hit(tmp_path: Path):
    hits = _hits(
        tmp_path,
        """
        from django.db import models

        class Tag(models.Model):
            pass

        class Rule(models.Model):
            tags = models.ManyToManyField(Tag)
        """,
        """
        def f(tag):
            for r in Rule.objects.filter(active=True):
                r.tags.add(tag)
            for r in Rule.objects.all():
                r.tags.all().update(name="x")
        """,
    )
    assert hits == []


# -- review fixes --------------------------------------------------------------


def test_sa_option_from_a_helper_hides_the_load(tmp_path: Path):
    hits = _hits(
        tmp_path,
        """
        class Order:
            items = relationship("Item")
        """,
        """
        def f(session):
            for o in session.query(Order).options(eager_items()).all():
                print(o.items)
            for o in session.query(Order).options(load_only("id")).all():
                print(o.items)
        """,
    )
    assert [h.line for h in hits] == [6]


def test_django_splat_select_related_hides_the_load(tmp_path: Path):
    hits = _hits(
        tmp_path,
        """
        from django.db import models

        class Order(models.Model):
            customer = models.ForeignKey("Customer", on_delete=models.CASCADE)
        """,
        """
        def f(fields):
            for o in Order.objects.select_related(*fields):
                print(o.customer)
        """,
    )
    assert hits == []


def test_django_eager_custom_manager_is_not_typed(tmp_path: Path):
    hits = _hits(
        tmp_path,
        """
        from django.db import models

        class OrderManager(models.Manager):
            def get_queryset(self):
                return super().get_queryset().select_related("customer")

        class Order(models.Model):
            customer = models.ForeignKey("Customer", on_delete=models.CASCADE)
            objects = OrderManager()

        class RushOrder(Order):
            pass

        class Invoice(models.Model):
            customer = models.ForeignKey("Customer", on_delete=models.CASCADE)
            objects = PlainQuerySet.as_manager()
        """,
        """
        def f():
            for o in Order.objects.all():
                print(o.customer)
            for o in RushOrder.objects.all():
                print(o.customer)
            for i in Invoice.objects.all():
                print(i.customer)
        """,
    )
    assert [h.path[0] for h in hits] == ["Invoice.customer"]


def test_model_index_collision_across_orms_drops_the_attr(tmp_path: Path):
    _require_python()
    a = _write(tmp_path, "a.py", 'class Item:\n    variants = relationship("Variant")\n')
    b = _write(
        tmp_path,
        "b.py",
        'from django.db import models\n\nclass Item(models.Model):\n'
        '    variants = models.ManyToManyField("Variant")\n',
    )
    index = build_model_index([a, b], lambda p: Path(p).read_bytes())
    assert index.relations["Item"] == {}


def test_loop_target_rebound_by_inner_for_no_hit(tmp_path: Path):
    hits = _hits(
        tmp_path,
        """
        class Order:
            items = relationship("Item")
            parts = relationship("Part")
        """,
        """
        def f(session):
            for o in session.query(Order).all():
                for o in o.parts:
                    pass
                print(o.items)
        """,
    )
    assert hits == []


def test_fix_is_advisory_and_not_batchable():
    from repowise.core.analysis.health.perf.actionability import BATCHABLE_MARKERS, assess_fix

    fix = assess_fix(_KIND, (_KIND,), "db", [{}], cross_function=False).fix
    assert (fix.strategy, fix.safety) == ("eager_load_relationship", "advisory")
    assert _KIND not in BATCHABLE_MARKERS
