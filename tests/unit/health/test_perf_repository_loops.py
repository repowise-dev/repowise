"""Unit tests for the typed Java repository / mapper call in a loop.

Each fixture is a small Java tree parsed by the real ingestion parser, so the
heritage, imports, annotations and type references the pass reads are the ones
an index would hold. ``walked`` lists only the file being scored.
"""

from __future__ import annotations

import textwrap
from datetime import datetime
from pathlib import Path

import pytest

from repowise.core.analysis.health.complexity import FileComplexity, PerfHit
from repowise.core.analysis.health.perf.lazy_load import collect_lazy_loads
from repowise.core.analysis.health.perf.repository_loops import persistence_types
from repowise.core.ingestion.models import FileInfo
from repowise.core.ingestion.parser import parse_file


def _require_java() -> None:
    from repowise.core.ingestion.parser import _get_language

    if _get_language("java") is None:
        pytest.skip("tree-sitter language pack missing for java")


def _parse(tmp_path: Path, rel: str, src: str, *, is_test: bool = False):
    p = tmp_path / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(textwrap.dedent(src), encoding="utf-8")
    info = FileInfo(
        path=rel, abs_path=str(p), language="java", size_bytes=p.stat().st_size, git_hash="",
        last_modified=datetime.now(), is_test=is_test, is_config=False, is_api_contract=False,
        is_entry_point=False,
    )
    return parse_file(info, p.read_bytes())


def _hits(tmp_path: Path, files: dict[str, str], scored: str, *, is_test: bool = False,
          seeded: list[PerfHit] | None = None) -> list[PerfHit]:
    _require_java()
    parsed = {rel: _parse(tmp_path, rel, src, is_test=is_test and rel == scored)
              for rel, src in files.items()}
    fcx = FileComplexity(functions=[], classes=[])
    fcx.perf_hits.extend(seeded or [])
    collect_lazy_loads([(parsed[scored], fcx)], list(parsed.values()))
    return [h for h in fcx.perf_hits if h.kind == "io_in_loop" and h not in (seeded or [])]


_SCAN_CONFIG = """
    package com.shop.config;
    import org.mybatis.spring.annotation.MapperScan;
    @MapperScan({"com.shop.mapper"})
    public class MyBatisConfig {}
"""
_BRAND_MAPPER = """
    package com.shop.mapper;
    public interface BrandMapper { int insert(Brand row); }
"""
_SERVICE = "src/com/shop/service/BrandService.java"


def _mybatis(service_src: str) -> dict[str, str]:
    return {
        "src/com/shop/config/MyBatisConfig.java": _SCAN_CONFIG,
        "src/com/shop/mapper/BrandMapper.java": _BRAND_MAPPER,
        _SERVICE: service_src,
    }


def test_mapper_scan_package_insert_per_row(tmp_path: Path):
    hits = _hits(tmp_path, _mybatis("""
        package com.shop.service;
        import com.shop.mapper.BrandMapper;
        public class BrandService {
            private BrandMapper brandMapper;
            public int create(java.util.List<Brand> rows) {
                for (Brand row : rows) {
                    row.setSort(0);
                    brandMapper.insert(row);
                }
                return rows.size();
            }
        }
    """), _SERVICE)
    assert len(hits) == 1
    hit = hits[0]
    assert (hit.line, hit.function, hit.detail, hit.path) == (9, "create", "db", ())
    assert hit.sink_call == "brandMapper.insert"
    assert hit.loop_line == 7


def test_spring_data_repository_through_own_base(tmp_path: Path):
    files = {
        "src/a/BaseRepository.java": """
            package a;
            import org.springframework.data.jpa.repository.JpaRepository;
            public interface BaseRepository<T> extends JpaRepository<T, Long> {}
        """,
        "src/a/ActivityRepository.java": """
            package a;
            public interface ActivityRepository extends BaseRepository<Activity> {}
        """,
        "src/a/Adapter.java": """
            package a;
            public class Adapter {
                private final ActivityRepository activities;
                void update(Account account) {
                    for (Activity a : account.getActivities()) {
                        this.activities.persist(a);
                    }
                }
            }
        """,
    }
    hits = _hits(tmp_path, files, "src/a/Adapter.java")
    assert [(h.line, h.function) for h in hits] == [(7, "update")]


def test_mapper_annotation_and_parameter_receiver(tmp_path: Path):
    files = {
        "src/b/StockMapper.java": """
            package b;
            import org.apache.ibatis.annotations.Mapper;
            @Mapper
            public interface StockMapper { Stock selectByPrimaryKey(Long id); }
        """,
        "src/b/Locker.java": """
            package b;
            public class Locker {
                void lock(StockMapper mapper, java.util.List<Item> items) {
                    for (Item item : items) {
                        Stock s = mapper.selectByPrimaryKey(item.getSkuId());
                    }
                }
            }
        """,
    }
    assert [h.line for h in _hits(tmp_path, files, "src/b/Locker.java")] == [6]


def test_repository_named_interface_without_spring_base_is_not_typed(tmp_path: Path):
    files = {
        "src/c/JpaRepository.java": "package c;\npublic interface JpaRepository<T, K> {}\n",
        "src/c/UserRepository.java": """
            package c;
            public interface UserRepository extends JpaRepository<User, Long> {}
        """,
        "src/c/Svc.java": """
            package c;
            public class Svc {
                private UserRepository userRepository;
                void f(java.util.List<User> users) {
                    for (User u : users) { userRepository.save(u); }
                }
            }
        """,
    }
    assert _hits(tmp_path, files, "src/c/Svc.java") == []


@pytest.mark.parametrize(
    "body",
    [
        # Header call runs once.
        "for (Brand b : brandMapper.selectAll()) { b.setSort(0); }",
        # A local of another type shadows the typed field.
        "java.util.Map<Long, Brand> brandMapper = null;\n"
        "for (Brand b : rows) { brandMapper.get(b.getId()); }",
        # A constant iterable is a fixed number of calls.
        "for (Long id : List.of(1L, 2L)) { brandMapper.insert(null); }",
    ],
)
def test_not_a_per_row_call(tmp_path: Path, body: str):
    service = (
        "package com.shop.service;\nimport com.shop.mapper.BrandMapper;\n"
        "public class BrandService {\n  private BrandMapper brandMapper;\n"
        "  void f(java.util.List<Brand> rows) {\n" + body + "\n  }\n}\n"
    )
    assert _hits(tmp_path, _mybatis(service), _SERVICE) == []


_LOOP_SERVICE = """
    package com.shop.service;
    import com.shop.mapper.BrandMapper;
    public class BrandService {
        private BrandMapper brandMapper;
        void f(java.util.List<Brand> rows) {
            for (Brand b : rows) { brandMapper.insert(b); }
        }
    }
"""


def test_test_files_are_skipped(tmp_path: Path):
    assert _hits(tmp_path, _mybatis(_LOOP_SERVICE), _SERVICE, is_test=True) == []


def test_line_the_walker_flagged_is_not_repeated(tmp_path: Path):
    seeded = [PerfHit("io_in_loop", 7, "f", "db")]
    assert _hits(tmp_path, _mybatis(_LOOP_SERVICE), _SERVICE, seeded=seeded) == []


def test_ambiguous_simple_name_is_dropped(tmp_path: Path):
    _require_java()
    files = _mybatis(_LOOP_SERVICE)
    files["src/com/other/BrandMapper.java"] = "package com.other;\npublic class BrandMapper {}\n"
    parsed = [_parse(tmp_path, rel, src) for rel, src in files.items()]
    assert "BrandMapper" not in persistence_types(parsed)


def test_update_scoring_only_the_caller_matches_a_full_run(tmp_path: Path):
    # An update walks the changed caller only; the unchanged config and mapper
    # still come in through parsed_files, so the result equals a full run.
    _require_java()
    parsed = [_parse(tmp_path, rel, src) for rel, src in _mybatis(_LOOP_SERVICE).items()]
    full = {pf.file_info.path: FileComplexity(functions=[], classes=[]) for pf in parsed}
    collect_lazy_loads([(pf, full[pf.file_info.path]) for pf in parsed], parsed)
    service = next(pf for pf in parsed if pf.file_info.path == _SERVICE)
    partial = FileComplexity(functions=[], classes=[])
    collect_lazy_loads([(service, partial)], parsed)
    assert partial.perf_hits == full[_SERVICE].perf_hits
    assert [h.line for h in partial.perf_hits] == [7]


def test_qualified_field_and_local_receivers_are_typed(tmp_path: Path):
    hits = _hits(tmp_path, _mybatis("""
        package com.shop.service;
        public class BrandService {
            private com.shop.mapper.BrandMapper brandMapper;
            void f(java.util.List<Brand> rows) {
                for (Brand b : rows) { brandMapper.insert(b); }
            }
            void g(java.util.List<Brand> rows) {
                com.shop.mapper.BrandMapper local = brandMapper;
                for (Brand b : rows) { local.insert(b); }
            }
        }
    """), _SERVICE)
    assert sorted((h.line, h.function) for h in hits) == [(6, "f"), (10, "g")]


def test_collection_of_mappers_is_not_a_mapper(tmp_path: Path):
    hits = _hits(tmp_path, _mybatis("""
        package com.shop.service;
        import com.shop.mapper.BrandMapper;
        public class BrandService {
            private java.util.List<BrandMapper> brandMapper;
            void f(java.util.List<Brand> rows) {
                for (Brand b : rows) { brandMapper.add(null); }
            }
        }
    """), _SERVICE)
    assert hits == []


def test_java_rows_are_held_back_and_counted_not_dropped(tmp_path: Path, monkeypatch):
    from repowise.core.analysis import finding_registry
    from repowise.core.analysis.finding_registry import LanguageGate, split_gated
    from repowise.core.analysis.health.biomarkers.base import FileContext
    from repowise.core.analysis.health.biomarkers.io_in_loop import BIOMARKER

    # A gated Java cell on both layers, whatever state today's table holds.
    monkeypatch.setattr(
        finding_registry,
        "LANGUAGE_GATES",
        tuple(
            LanguageGate(layer, "java", 0.1, "test", "2026-10-15", "test")
            for layer in ("health", "performance")
        ),
    )

    hits = _hits(tmp_path, _mybatis(_LOOP_SERVICE), _SERVICE)
    ctx = FileContext(file_path=_SERVICE, language="java", nloc=8, has_test_file=False,
                      module=None, perf_hits=hits)
    rows = [{"file_path": _SERVICE, "biomarker_type": r.biomarker_type}
            for r in BIOMARKER.detect(ctx)]
    assert len(rows) == 1
    for layer in ("health", "performance"):
        shown, held = split_gated(layer, rows, kind="biomarker_type", get=dict.get)
        assert shown == [] and held["java"]["count"] == 1
        shown, held = split_gated(layer, rows, kind="biomarker_type", get=dict.get,
                                  include_unverified=True)
        assert shown == rows and held == {}


def test_one_shot_iterables_reach_the_java_pass(tmp_path: Path):
    _require_java()
    parsed = {rel: _parse(tmp_path, rel, src) for rel, src in _mybatis(_LOOP_SERVICE).items()}
    fcx = FileComplexity(functions=[], classes=[])
    collect_lazy_loads(
        (pair for pair in [(parsed[_SERVICE], fcx)]), (pf for pf in parsed.values())
    )
    assert [h.line for h in fcx.perf_hits] == [7]


def test_same_named_base_elsewhere_does_not_type_a_child(tmp_path: Path):
    files = {
        "src/data/BaseRepository.java": """
            package data;
            import org.springframework.data.jpa.repository.JpaRepository;
            public interface BaseRepository<T> extends JpaRepository<T, Long> {}
        """,
        "src/cache/BaseRepository.java": """
            package cache;
            public interface BaseRepository<T> { T get(Object key); }
        """,
        "src/cache/SessionRepository.java": """
            package cache;
            public interface SessionRepository extends BaseRepository<Session> {}
        """,
        "src/cache/Svc.java": """
            package cache;
            public class Svc {
                private SessionRepository sessions;
                void f(java.util.List<String> ids) {
                    for (String id : ids) { sessions.get(id); }
                }
            }
        """,
    }
    _require_java()
    parsed = [_parse(tmp_path, rel, src) for rel, src in files.items()]
    types = persistence_types(parsed)
    assert "SessionRepository" not in types and "BaseRepository" not in types
    assert _hits(tmp_path, files, "src/cache/Svc.java") == []


_DATA_BASE = """
    package com.data;
    import org.springframework.data.jpa.repository.JpaRepository;
    public interface BaseRepository<T> extends JpaRepository<T, Long> {}
"""


@pytest.mark.parametrize(
    ("child_src", "typed"),
    [
        # Imports a same-named base from a library the repo does not contain.
        ("package cache;\nimport org.ehcache.BaseRepository;\n"
         "public interface CacheRepository extends BaseRepository<Session> {}\n", False),
        # No import and another package: the name cannot mean com.data.BaseRepository.
        ("package cache;\npublic interface CacheRepository extends BaseRepository<Session> {}\n",
         False),
        ("package cache;\nimport com.data.BaseRepository;\n"
         "public interface CacheRepository extends BaseRepository<Session> {}\n", True),
        ("package cache;\nimport com.data.*;\n"
         "public interface CacheRepository extends BaseRepository<Session> {}\n", True),
    ],
)
def test_child_typed_only_when_its_base_name_resolves_here(
    tmp_path: Path, child_src: str, typed: bool
):
    _require_java()
    parsed = [
        _parse(tmp_path, "src/com/data/BaseRepository.java", _DATA_BASE),
        _parse(tmp_path, "src/cache/CacheRepository.java", child_src),
    ]
    assert ("CacheRepository" in persistence_types(parsed)) is typed


def test_spring_base_through_a_wildcard_import(tmp_path: Path):
    _require_java()
    parsed = [_parse(tmp_path, "src/a/UserRepository.java", """
        package a;
        import org.springframework.data.repository.*;
        public interface UserRepository extends CrudRepository<User, Long> {}
    """)]
    assert persistence_types(parsed) == frozenset({"UserRepository"})
