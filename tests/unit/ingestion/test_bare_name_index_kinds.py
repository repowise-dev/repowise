"""Tier 3 must not offer a data member as a function.

``_resolve_free_call``'s global-unique tier answers a bare name with any
repo symbol carrying that name. The index it consults holds every symbol,
kinds included that cannot be called, so ``x.ok()`` on a ``Result`` resolved
to a struct field ``ok: bool`` declared in an unrelated file. A field is not
callable, so every such edge was wrong.

The two negative tests below are the ones that matter: the predicate is
deliberately narrower than "not a function", because ``class`` is a
constructor and ``variable`` is both a TypeScript arrow function and a Rust
tuple enum variant. Denying by function-ness would delete real edges.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from repowise.core.ingestion.call_resolver import _NON_CALLABLE_KINDS, CallResolver
from repowise.core.ingestion.models import FileInfo, ParsedFile
from repowise.core.ingestion.parser import parse_file


def _file_info(rel: str, abs_: Path, lang: str) -> FileInfo:
    return FileInfo(
        path=rel,
        abs_path=str(abs_),
        language=lang,  # type: ignore[arg-type]
        size_bytes=abs_.stat().st_size,
        git_hash="",
        last_modified=datetime.now(),
        is_test=False,
        is_config=False,
        is_api_contract=False,
        is_entry_point=False,
    )


def _parse_all(tmp_path: Path, files: dict[str, tuple[str, str]]) -> dict[str, ParsedFile]:
    out: dict[str, ParsedFile] = {}
    for rel, (lang, content) in files.items():
        abs_ = tmp_path / rel
        abs_.parent.mkdir(parents=True, exist_ok=True)
        abs_.write_text(content)
        out[rel] = parse_file(_file_info(rel, abs_, lang), content.encode("utf-8"))
    return out


def _edges(parsed: dict[str, ParsedFile], tmp_path: Path) -> list[tuple[str, str, str]]:
    resolver = CallResolver(
        parsed, {p: set() for p in parsed}, repo_path=str(tmp_path)
    )
    return [
        (rc.caller_id, rc.callee_id, rc.origin)
        for path, pf in parsed.items()
        for rc in resolver.resolve_file(path, pf.calls)
    ]


def _kinds(parsed: dict[str, ParsedFile], name: str) -> set[str]:
    return {s.kind for pf in parsed.values() for s in pf.symbols if s.name == name}


class TestFieldsAreNotCallable:
    def test_a_struct_field_does_not_answer_a_bare_name(self, tmp_path: Path) -> None:
        """``.ok()`` on a Result must not bind to a field named ``ok``."""
        parsed = _parse_all(
            tmp_path,
            {
                "model.rs": ("rust", "pub struct Reply {\n    pub ok: bool,\n}\n"),
                "caller.rs": (
                    "rust",
                    "pub fn run(v: &str) -> Option<u64> {\n"
                    "    v.parse::<u64>().ok()\n"
                    "}\n",
                ),
            },
        )
        # Guard the premise: `ok` is indexed, and only as a field.
        assert _kinds(parsed, "ok") == {"property"}
        assert not [
            e for e in _edges(parsed, tmp_path) if e[1] == "model.rs::ok"
        ]

    def test_a_real_function_still_answers_the_same_bare_name(
        self, tmp_path: Path
    ) -> None:
        """The tier itself is untouched -- only non-callable answers are refused."""
        parsed = _parse_all(
            tmp_path,
            {
                "util.rs": ("rust", "pub fn only_one_of_these() -> u8 {\n    1\n}\n"),
                "caller.rs": (
                    "rust",
                    "pub fn run() -> u8 {\n    only_one_of_these()\n}\n",
                ),
            },
        )
        assert (
            "caller.rs::run",
            "util.rs::only_one_of_these",
            "global_unique",
        ) in _edges(parsed, tmp_path)

    def test_a_field_never_makes_a_shadowed_name_unique(self, tmp_path: Path) -> None:
        """Uniqueness is judged before the callability test, never after.

        A name declared by both a field and a function is ambiguous, and must
        stay refused. Filtering the candidate pool first would make the field
        vanish, leave one answer and fire the tier -- measured at +916 new
        0.50-confidence edges on goose, on a tier hand-read at 28.6%.
        """
        parsed = _parse_all(
            tmp_path,
            {
                "model.rs": ("rust", "pub struct Reply {\n    pub emit: bool,\n}\n"),
                "util.rs": ("rust", "pub fn emit() -> u8 {\n    1\n}\n"),
                "caller.rs": ("rust", "pub fn run() -> u8 {\n    emit()\n}\n"),
            },
        )
        assert _kinds(parsed, "emit") == {"property", "function"}
        assert not [
            e for e in _edges(parsed, tmp_path) if e[0] == "caller.rs::run"
        ]


class TestThePredicateStaysNarrow:
    """Kinds that look non-callable but are not. Regression guards."""

    def test_only_property_is_denied(self) -> None:
        assert frozenset({"property"}) == _NON_CALLABLE_KINDS

    def test_a_rust_tuple_enum_variant_stays_callable(self, tmp_path: Path) -> None:
        """``enum_variant`` maps to ``variable``; ``Shape::Circle(1.0)`` is a call."""
        parsed = _parse_all(
            tmp_path,
            {
                "shape.rs": ("rust", "pub enum Shape {\n    Circle(f64),\n}\n"),
                "caller.rs": (
                    "rust",
                    "pub fn run() -> Shape {\n    Circle(1.0)\n}\n",
                ),
            },
        )
        assert "variable" in _kinds(parsed, "Circle")
        assert "variable" not in _NON_CALLABLE_KINDS

    def test_a_typescript_callable_const_stays_callable(self, tmp_path: Path) -> None:
        """A const whose initialiser is not syntactically a function is a
        ``variable`` -- and is still called.

        An arrow or function expression is already classified ``function``, so
        ``variable`` is precisely the bucket holding factory results and
        ``.bind()`` handles. zod alone has 2,173 grounded call edges landing on
        one, which is why this kind cannot join the deny set.
        """
        parsed = _parse_all(
            tmp_path,
            {
                "util.ts": (
                    "typescript",
                    "import { base } from './base';\nexport const doThing = base.bind(null);\n",
                ),
                "caller.ts": (
                    "typescript",
                    "export function run() {\n  return doThing();\n}\n",
                ),
            },
        )
        assert _kinds(parsed, "doThing") == {"variable"}
        assert [e for e in _edges(parsed, tmp_path) if e[1] == "util.ts::doThing"]


def _edges_with_imports(
    parsed: dict[str, ParsedFile],
    tmp_path: Path,
    import_targets: dict[str, set[str]],
) -> list[tuple[str, str, str]]:
    """``_edges`` passes no imports, so the import tiers never fire there."""
    resolver = CallResolver(parsed, import_targets, repo_path=str(tmp_path))
    return [
        (rc.caller_id, rc.callee_id, rc.origin)
        for path, pf in parsed.items()
        for rc in resolver.resolve_file(path, pf.calls)
    ]


class TestTheImportMergedTierRefusesTheSame:
    """The rule is the rung's, not tier 3's.

    ``import_merged`` answers a bare name from every imported file's symbols and
    was minting the field edge at 0.85 -- above the tier that declines it. Both
    cases declare the name TWICE, because that is the only shape where the tiers
    disagree: tier 3 refuses an ambiguous name outright, so a single declaration
    is refused either way and would prove nothing.
    """

    def test_an_imported_field_does_not_answer_a_bare_name(
        self, tmp_path: Path
    ) -> None:
        parsed = _parse_all(
            tmp_path,
            {
                "model.rs": (
                    "rust",
                    "pub struct Index {\n    pub map: Vec<usize>,\n}\n",
                ),
                "other.rs": (
                    "rust",
                    "pub struct Other {\n    pub map: Vec<u8>,\n}\n",
                ),
                # A BARE call, not `v.map(..)`: a call with a receiver takes
                # the member path and never reaches this tier.
                "caller.rs": ("rust", "pub fn run(v: u64) -> u64 {\n    map(v)\n}\n"),
            },
        )
        assert _kinds(parsed, "map") == {"property"}
        edges = _edges_with_imports(
            parsed, tmp_path, {"caller.rs": {"model.rs"}, "model.rs": set(), "other.rs": set()}
        )
        assert not [e for e in edges if e[1] == "model.rs::map"]

    def test_an_imported_function_still_answers(self, tmp_path: Path) -> None:
        """Control: the tier is untouched where the answer really is callable."""
        parsed = _parse_all(
            tmp_path,
            {
                "util.rs": ("rust", "pub fn render() -> u8 {\n    1\n}\n"),
                "other.rs": ("rust", "pub fn render() -> u8 {\n    2\n}\n"),
                "caller.rs": ("rust", "pub fn run() -> u8 {\n    render()\n}\n"),
            },
        )
        edges = _edges_with_imports(
            parsed, tmp_path, {"caller.rs": {"util.rs"}, "util.rs": set(), "other.rs": set()}
        )
        assert [e for e in edges if e[1] == "util.rs::render" and e[2] == "import_merged"]
