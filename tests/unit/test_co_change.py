"""The shared co-change record reader."""

from __future__ import annotations

import json

from repowise.core.co_change import canonical_pair, parse_partners
from repowise.core.test_paths import is_test_to_production_pair


class TestTolerance:
    def test_absent_and_malformed_cells_yield_nothing(self) -> None:
        for raw in (None, "", "not json", "{", b"nope"):
            assert parse_partners(raw) == []

    def test_non_list_document_yields_nothing(self) -> None:
        assert parse_partners('{"file_path": "a.py"}') == []
        assert parse_partners("5") == []

    def test_accepts_an_already_decoded_list(self) -> None:
        (p,) = parse_partners([{"file_path": "a.py", "co_change_count": 1}])
        assert p.file_path == "a.py"


class TestParsePartners:
    def test_reads_the_canonical_field_names(self) -> None:
        raw = json.dumps(
            [{"file_path": "a.py", "co_change_count": 4.25, "last_co_change": "2026-01-02"}]
        )
        (p,) = parse_partners(raw)
        assert (p.file_path, p.weight, p.last_co_change) == ("a.py", 4.25, "2026-01-02")

    def test_reads_the_path_and_count_aliases(self) -> None:
        (p,) = parse_partners(json.dumps([{"path": "a.py", "count": 3}]))
        assert (p.file_path, p.weight) == ("a.py", 3.0)

    def test_keeps_the_fractional_weight(self) -> None:
        # The producer rounds to 2dp because the weight is a decayed sum, so
        # truncating it would move a pair across a severity threshold.
        (p,) = parse_partners(json.dumps([{"file_path": "a.py", "co_change_count": 7.9}]))
        assert p.weight == 7.9

    def test_drops_records_with_no_path_or_a_bad_weight(self) -> None:
        raw = json.dumps(
            [
                {"co_change_count": 5},
                {"file_path": "", "co_change_count": 5},
                {"file_path": "a.py", "co_change_count": "many"},
                {"file_path": "b.py", "co_change_count": 1},
            ]
        )
        assert [p.file_path for p in parse_partners(raw)] == ["b.py"]

    def test_a_bare_string_partner_does_not_raise(self) -> None:
        raw = json.dumps(["a.py", {"file_path": "b.py", "co_change_count": 2}])
        assert [p.file_path for p in parse_partners(raw)] == ["b.py"]

    def test_orders_strongest_first(self) -> None:
        raw = json.dumps(
            [
                {"file_path": "a.py", "co_change_count": 1},
                {"file_path": "b.py", "co_change_count": 9},
                {"file_path": "c.py", "co_change_count": 5},
            ]
        )
        assert [p.file_path for p in parse_partners(raw)] == ["b.py", "c.py", "a.py"]

    def test_a_non_string_date_becomes_none(self) -> None:
        (p,) = parse_partners(json.dumps([{"file_path": "a.py", "count": 1, "last_co_change": 7}]))
        assert p.last_co_change is None


class TestCanonicalPair:
    def test_orders_the_pair_the_same_way_from_either_side(self) -> None:
        assert canonical_pair("b.py", "a.py") == canonical_pair("a.py", "b.py") == ("a.py", "b.py")


class TestIsTestToProductionPair:
    def test_a_test_and_its_subject_are_a_pair(self) -> None:
        assert is_test_to_production_pair("tests/unit/test_a.py", "src/a.py")

    def test_two_production_files_are_not(self) -> None:
        assert not is_test_to_production_pair("src/a.py", "src/b.py")

    def test_two_tests_are_not(self) -> None:
        assert not is_test_to_production_pair("tests/unit/test_a.py", "tests/unit/test_b.py")


class TestRawRecord:
    def test_carries_the_verbatim_record_for_fields_not_modelled(self) -> None:
        (p,) = parse_partners(json.dumps([{"file_path": "a.py", "count": 1, "frequency": 12}]))
        assert p.record["frequency"] == 12

    def test_the_record_is_excluded_from_equality(self) -> None:
        a = parse_partners(json.dumps([{"file_path": "a.py", "count": 1, "extra": 1}]))
        b = parse_partners(json.dumps([{"file_path": "a.py", "count": 1}]))
        assert a == b


class TestSupportAndConfidence:
    def test_reads_the_support_count_and_both_commit_totals(self) -> None:
        raw = json.dumps(
            [
                {
                    "file_path": "a.py",
                    "co_change_count": 4.25,
                    "frequency": 9,
                    "self_commits": 10,
                    "partner_commits": 30,
                }
            ]
        )
        (p,) = parse_partners(raw)
        assert p.support == 9
        assert (p.self_commits, p.partner_commits) == (10, 30)

    def test_counts_missing_or_malformed_read_as_zero(self) -> None:
        (p,) = parse_partners(
            json.dumps([{"file_path": "a.py", "count": 1, "frequency": "many"}])
        )
        assert p.support == 0

    def test_weight_keeps_its_fraction(self) -> None:
        """The weight is a decayed sum, so truncating it moves severity bands."""
        (p,) = parse_partners(json.dumps([{"file_path": "a.py", "co_change_count": 8.9}]))
        assert p.weight == 8.9


class TestStructuralLabel:
    def test_reads_the_label(self) -> None:
        (p,) = parse_partners(
            json.dumps([{"file_path": "a.py", "count": 1, "structural": "unexplained"}])
        )
        assert p.structural == "unexplained"

    def test_an_unlabelled_record_is_none_not_unexplained(self) -> None:
        """An older index must not have its silence read as a finding."""
        (p,) = parse_partners(json.dumps([{"file_path": "a.py", "count": 1}]))
        assert p.structural is None

    def test_a_non_string_label_is_none(self) -> None:
        (p,) = parse_partners(
            json.dumps([{"file_path": "a.py", "count": 1, "structural": 3}])
        )
        assert p.structural is None
