"""SQL health markers: routine CCN + statement smells.

Acceptance rule from the marker plan: zero false fires on the negative set.
Every smell test has a paired negative asserting silence.
"""

from __future__ import annotations

from types import SimpleNamespace

from repowise.core.analysis.health.biomarkers.base import FileContext
from repowise.core.analysis.health.biomarkers.registry import detect_all
from repowise.core.analysis.health.models import Severity
from repowise.core.analysis.health.scoring import (
    biomarker_dimension,
    dimensions_for,
    score_file,
)
from repowise.core.analysis.health.sql_complexity import (
    _body_ccn,
    _statement_smells,
    walk_sql_file,
)


def _fi(name: str = "schema.sql") -> SimpleNamespace:
    return SimpleNamespace(abs_path=f"/repo/{name}", path=name, language="sql")


def _walk(sql: str):
    return walk_sql_file(_fi(), sql.encode("utf-8"))


def _smell_kinds(sql: str) -> list[str]:
    return sorted(h.kind for h in _statement_smells(sql, None))


# ---------------------------------------------------------------------------
# Routine CCN (text tier)
# ---------------------------------------------------------------------------


class TestBodyCcn:
    def test_flat_body_is_one(self) -> None:
        assert _body_ccn("BEGIN RETURN 1; END") == 1

    def test_plpgsql_if_elsif(self) -> None:
        body = "BEGIN IF a THEN RETURN 1; ELSIF b THEN RETURN 2; END IF; END"
        # 1 + IF + ELSIF (END IF's token discounted)
        assert _body_ccn(body) == 3

    def test_while_loop_not_double_counted(self) -> None:
        body = "BEGIN WHILE x < 10 LOOP SET x = x + 1; END LOOP; END"
        # PL/pgSQL WHILE..LOOP nets to a single decision point.
        assert _body_ccn(body) == 2

    def test_tsql_while(self) -> None:
        body = "BEGIN WHILE @x < 10 BEGIN SET @x = @x + 1 END END"
        assert _body_ccn(body) == 2

    def test_boolean_operators_count(self) -> None:
        body = "BEGIN IF a > 0 AND b > 0 OR c THEN RETURN 1; END IF; END"
        assert _body_ccn(body) == 4

    def test_between_and_discounted(self) -> None:
        body = "BEGIN SELECT 1 WHERE x BETWEEN 1 AND 10; END"
        assert _body_ccn(body) == 1

    def test_comments_and_strings_ignored(self) -> None:
        body = "BEGIN -- IF this OR that\n SELECT 'WHILE AND OR'; /* WHEN */ END"
        assert _body_ccn(body) == 1


class TestWalkRoutines:
    def test_postgres_function_metrics(self) -> None:
        sql = (
            "CREATE FUNCTION add_em(a INT, b INT) RETURNS INT AS $$\n"
            "BEGIN\n"
            "    IF a > 0 AND b > 0 THEN\n"
            "        RETURN a + b;\n"
            "    END IF;\n"
            "    RETURN 0;\n"
            "END;\n"
            "$$ LANGUAGE plpgsql;\n"
        )
        fcx = _walk(sql)
        assert [f.name for f in fcx.functions] == ["add_em"]
        fn = fcx.functions[0]
        assert fn.ccn == 3  # IF + AND
        assert fn.start_line == 1
        assert fn.nloc > 0
        assert fcx.file_nloc > 0

    def test_tsql_procedure_metrics(self) -> None:
        sql = (
            "CREATE PROCEDURE dbo.process_orders @status INT\n"
            "AS\nBEGIN\n"
            "    IF @status = 1\n"
            "    BEGIN\n"
            "        UPDATE orders SET state = 'x' WHERE id = @status;\n"
            "    END\n"
            "    WHILE @status < 10\n"
            "    BEGIN\n"
            "        SET @status = @status + 1;\n"
            "    END\n"
            "END\n"
        )
        fcx = _walk(sql)
        assert [f.name for f in fcx.functions] == ["process_orders"]
        assert fcx.functions[0].ccn == 3  # IF + WHILE

    def test_high_ccn_emits_hit(self) -> None:
        branches = "".join(f"    IF x = {i} THEN RETURN {i}; END IF;\n" for i in range(12))
        sql = f"CREATE FUNCTION f() RETURNS INT AS $$\nBEGIN\n{branches}END;\n$$;"
        fcx = _walk(sql)
        hits = [h for h in fcx.perf_hits if h.kind == "sql_high_complexity"]
        assert len(hits) == 1
        assert int(hits[0].detail) == 13
        assert hits[0].function == "f"

    def test_simple_routine_emits_no_hit(self) -> None:
        sql = "CREATE FUNCTION f() RETURNS INT AS $$ SELECT 1 $$;"
        assert [h for h in _walk(sql).perf_hits if h.kind == "sql_high_complexity"] == []

    def test_plain_ddl_yields_no_functions(self) -> None:
        fcx = _walk("CREATE TABLE users (id INT);")
        assert fcx.functions == []


# ---------------------------------------------------------------------------
# Statement smells (AST tier): every positive has a paired negative
# ---------------------------------------------------------------------------


class TestSelectStar:
    def test_fires_in_view(self) -> None:
        assert _smell_kinds("CREATE VIEW v AS SELECT * FROM t") == ["sql_select_star"]

    def test_silent_in_ad_hoc_script(self) -> None:
        assert _smell_kinds("SELECT * FROM t") == []

    def test_silent_for_qualified_star(self) -> None:
        assert _smell_kinds("CREATE VIEW v AS SELECT t.* FROM t") == []

    def test_silent_for_count_star(self) -> None:
        assert _smell_kinds("CREATE VIEW v AS SELECT COUNT(*) FROM t") == []

    def test_one_finding_per_view(self) -> None:
        sql = "CREATE VIEW v AS SELECT * FROM (SELECT * FROM t) sub"
        assert _smell_kinds(sql) == ["sql_select_star"]


class TestUpdateDeleteWithoutWhere:
    def test_update_fires(self) -> None:
        hits = _statement_smells("UPDATE users SET active = 0", None)
        assert [h.kind for h in hits] == ["sql_update_delete_without_where"]
        assert "update users" in hits[0].detail

    def test_delete_fires(self) -> None:
        assert _smell_kinds("DELETE FROM sessions") == ["sql_update_delete_without_where"]

    def test_silent_with_where(self) -> None:
        assert _smell_kinds("UPDATE users SET active = 0 WHERE id = 1") == []
        assert _smell_kinds("DELETE FROM sessions WHERE expired = 1") == []

    def test_silent_on_dialect_garble(self) -> None:
        # A backtick-quoted MySQL UPDATE read under the permissive default
        # dialect garbles the tokenization (found live on umami: the statement
        # HAS a WHERE that the broken parse loses). An unclean target name
        # means the parse is untrustworthy: no signal.
        sql = "UPDATE `website_event`\nSET x = 1\nWHERE url_query IS NOT NULL;"
        assert _smell_kinds(sql) == []


class TestCartesianJoin:
    def test_comma_join_without_where_fires(self) -> None:
        assert _smell_kinds("SELECT a.x, b.y FROM a, b") == ["sql_cartesian_join"]

    def test_comma_join_with_where_silent(self) -> None:
        assert _smell_kinds("SELECT a.x FROM a, b WHERE a.id = b.id") == []

    def test_explicit_cross_join_silent(self) -> None:
        assert _smell_kinds("SELECT a.x FROM a CROSS JOIN b") == []

    def test_keyed_join_silent(self) -> None:
        assert _smell_kinds("SELECT a.x FROM a JOIN b ON a.id = b.id") == []
        assert _smell_kinds("SELECT a.x FROM a JOIN b USING (id)") == []


class TestTemplatedFilesSilent:
    def test_dbt_model_yields_no_smells(self) -> None:
        sql = "select * from {{ ref('stg_orders') }}\nwhere x = 1"
        assert _statement_smells(sql, None) == []

    def test_jinja_block_yields_no_smells(self) -> None:
        sql = "{% set x = 1 %}\nUPDATE t SET y = 2"
        assert _statement_smells(sql, None) == []


class TestMalformedDegradation:
    def test_garbage_yields_empty_complexity(self) -> None:
        fcx = _walk("CREATE (((( garbage ;;;")
        assert fcx.functions == []
        assert fcx.perf_hits == []
        assert fcx.file_nloc >= 1


# ---------------------------------------------------------------------------
# Detector lift + scoring dimensions
# ---------------------------------------------------------------------------


def _ctx_for(sql: str) -> FileContext:
    fcx = _walk(sql)
    return FileContext(
        file_path="schema.sql",
        language="sql",
        nloc=fcx.file_nloc,
        has_test_file=False,
        module=None,
        perf_hits=fcx.perf_hits,
    )


class TestDetectors:
    def test_smells_become_findings(self) -> None:
        ctx = _ctx_for(
            "CREATE VIEW v AS SELECT * FROM t;\n"
            "UPDATE users SET active = 0;\n"
            "SELECT a.x FROM a, b;\n"
        )
        kinds = sorted(r.biomarker_type for r in detect_all(ctx))
        assert kinds == [
            "sql_cartesian_join",
            "sql_select_star",
            "sql_update_delete_without_where",
        ]

    def test_high_complexity_severity_scales(self) -> None:
        branches = "".join(f"IF x = {i} THEN RETURN {i}; END IF;\n" for i in range(25))
        ctx = _ctx_for(f"CREATE FUNCTION f() RETURNS INT AS $$\nBEGIN\n{branches}END $$;")
        results = [r for r in detect_all(ctx) if r.biomarker_type == "sql_high_complexity"]
        assert len(results) == 1
        assert results[0].severity == Severity.HIGH

    def test_clean_sql_yields_nothing(self) -> None:
        ctx = _ctx_for(
            "CREATE TABLE users (id INT PRIMARY KEY);\n"
            "CREATE VIEW v AS SELECT id FROM users WHERE id > 0;\n"
            "DELETE FROM users WHERE id = 1;\n"
        )
        assert [r for r in detect_all(ctx) if r.biomarker_type.startswith("sql_")] == []


class TestScoringDimensions:
    def test_every_sql_marker_excludes_defect(self) -> None:
        for name in (
            "sql_high_complexity",
            "sql_select_star",
            "sql_update_delete_without_where",
            "sql_cartesian_join",
        ):
            assert "defect" not in dimensions_for(name), name

    def test_homes(self) -> None:
        assert biomarker_dimension("sql_high_complexity") == "maintainability"
        assert biomarker_dimension("sql_select_star") == "maintainability"
        assert biomarker_dimension("sql_update_delete_without_where") == "maintainability"
        assert biomarker_dimension("sql_cartesian_join") == "performance"

    def test_defect_score_untouched_by_sql_findings(self) -> None:
        ctx = _ctx_for(
            "CREATE VIEW v AS SELECT * FROM t;\n"
            "UPDATE users SET active = 0;\n"
            "SELECT a.x FROM a, b;\n"
        )
        results = detect_all(ctx)
        assert results  # sanity: the smells fired
        scores, _ = score_file(results)
        assert scores["defect"] == 10.0
        assert scores["maintainability"] < 10.0
        assert scores["performance"] < 10.0
