"""Independent population probe for structural return-type call receivers.

The probe never invokes the return-type decision path. It runs the existing
resolver with every return-type lane disabled, then independently joins the
AST-carried inner call to stored signatures and repository type/method pairs.
All counts are distinct ``(file, line, target)`` sites.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path

from repowise.core.ingestion import call_resolver as resolver_module
from repowise.core.ingestion.graph.builder import GraphBuilder
from repowise.core.ingestion.models import ParsedFile
from repowise.core.ingestion.parser import parse_file
from repowise.core.ingestion.return_types import declared_return_type, normalize_return_type
from repowise.core.ingestion.traverser import FileTraverser
from repowise.core.ingestion.tsconfig_resolver import wire_tsconfig_resolver

_TYPE_KINDS = frozenset({"class", "struct", "interface", "enum", "trait", "impl"})


def _parse_repo(repo: Path, language: str) -> tuple[dict[str, ParsedFile], dict[str, bytes]]:
    parsed: dict[str, ParsedFile] = {}
    sources: dict[str, bytes] = {}
    for info in FileTraverser(repo).traverse():
        if info.language != language:
            continue
        try:
            source = Path(info.abs_path).read_bytes()
        except OSError:
            continue
        parsed[info.path] = parse_file(info, source)
        sources[info.path] = source
    return parsed, sources


def _resolved_edges(
    repo: Path,
    parsed: dict[str, ParsedFile],
    sources: dict[str, bytes],
    languages: frozenset[str],
) -> dict[tuple[str, int, str], dict[str, object]]:
    """Record one resolver run with an explicit language toggle."""

    resolved: dict[tuple[str, int, str], dict[str, object]] = {}
    original = resolver_module.CallResolver._resolve_one
    old_languages = resolver_module.PRODUCTION_RETURN_TYPE_CHAIN_LANGUAGES
    resolver_module.PRODUCTION_RETURN_TYPE_CHAIN_LANGUAGES = languages
    depth = 0

    def recording(self, file_path, call):
        nonlocal depth
        top_level = depth == 0
        depth += 1
        try:
            result = original(self, file_path, call)
        finally:
            depth -= 1
        if top_level and result is not None:
            resolved.setdefault(
                (file_path, call.line, call.target_name),
                {
                    "callee": result.callee_id,
                    "origin": result.origin,
                    "confidence": result.confidence,
                },
            )
        return result

    resolver_module.CallResolver._resolve_one = recording
    try:
        builder = GraphBuilder(repo)
        for parsed_file in parsed.values():
            builder.add_file(parsed_file)
        builder.set_source_map(sources)
        if "typescript" in {pf.file_info.language for pf in parsed.values()}:
            wire_tsconfig_resolver(builder, repo)
        builder.build()
    finally:
        resolver_module.CallResolver._resolve_one = original
        resolver_module.PRODUCTION_RETURN_TYPE_CHAIN_LANGUAGES = old_languages
    return resolved


def measure(repo: Path, language: str) -> dict[str, object]:
    parsed, sources = _parse_repo(repo, language)
    baseline = _resolved_edges(repo, parsed, sources, frozenset())
    symbols = {symbol.id: symbol for pf in parsed.values() for symbol in pf.symbols}
    known_types = {symbol.name for symbol in symbols.values() if symbol.kind in _TYPE_KINDS}
    methods: dict[tuple[str, str], set[str]] = defaultdict(set)
    for symbol in symbols.values():
        if symbol.parent_name:
            methods[(symbol.parent_name, symbol.name)].add(symbol.id)

    buckets: Counter[str] = Counter()
    examples: dict[str, list[dict[str, object]]] = defaultdict(list)
    seen: set[tuple[str, int, str]] = set()
    for path, pf in parsed.items():
        for call in pf.calls:
            receiver_call = getattr(call, "receiver_call", None)
            if receiver_call is None:
                continue
            key = (path, call.line, call.target_name)
            if key in seen:
                continue
            seen.add(key)
            buckets["structural_sites"] += 1
            inner = receiver_call
            inner_edge = baseline.get((path, call.line, inner.target_name))
            inner_id = str(inner_edge["callee"]) if inner_edge else None
            if inner_id is None:
                bucket = "refused_inner_unresolved"
            else:
                symbol = symbols.get(inner_id)
                raw = declared_return_type(symbol.signature or "") if symbol else None
                if symbol and symbol.kind in _TYPE_KINDS:
                    type_name = symbol.name
                else:
                    type_name = normalize_return_type(raw, language) if raw else None
                if type_name is None:
                    bucket = "refused_no_return_type"
                elif type_name not in known_types:
                    bucket = "refused_external_type"
                else:
                    candidates = methods.get((type_name, call.target_name), set())
                    before_edge = baseline.get(key)
                    before = str(before_edge["callee"]) if before_edge else None
                    if len(candidates) != 1:
                        bucket = (
                            "predicted_refused"
                            if not candidates and before is not None
                            else "refused_ambiguous_pair"
                        )
                    else:
                        candidate = next(iter(candidates))
                        if before is None:
                            bucket = "predicted_added"
                        elif before == candidate:
                            bucket = "predicted_agree"
                        else:
                            bucket = "predicted_retargeted"
            buckets[bucket] += 1
            if len(examples[bucket]) < 30:
                examples[bucket].append(
                    {"file": path, "line": call.line, "target": call.target_name}
                )

    result: dict[str, object] = {
        "repo": str(repo.resolve()),
        "language": language,
        "files": len(parsed),
        "buckets": dict(sorted(buckets.items())),
        "examples": dict(sorted(examples.items())),
    }
    return result


def compare(repo: Path, language: str) -> dict[str, object]:
    """Run control/treatment in one process and attribute every changed site."""

    parsed, sources = _parse_repo(repo, language)
    before = _resolved_edges(repo, parsed, sources, frozenset())
    after = _resolved_edges(repo, parsed, sources, frozenset({language}))
    symbols = {symbol.id: symbol for pf in parsed.values() for symbol in pf.symbols}
    known_types = {symbol.name for symbol in symbols.values() if symbol.kind in _TYPE_KINDS}
    structural = {
        (path, call.line, call.target_name)
        for path, pf in parsed.items()
        for call in pf.calls
        if getattr(call, "receiver_call", None) is not None
    }
    source_lines = {
        path: source.decode("utf-8", "replace").splitlines() for path, source in sources.items()
    }
    counts: Counter[str] = Counter()
    rows: dict[str, list[dict[str, object]]] = defaultdict(list)
    producer_kinds: Counter[str] = Counter()
    return_shapes: Counter[str] = Counter()
    identities: Counter[str] = Counter()
    for path, pf in parsed.items():
        for call in pf.calls:
            receiver_call = getattr(call, "receiver_call", None)
            if receiver_call is None:
                continue
            inner = receiver_call
            inner_edge = before.get((path, call.line, inner.target_name))
            symbol = symbols.get(str(inner_edge["callee"])) if inner_edge else None
            if symbol is None:
                producer_kinds["unresolved"] += 1
                return_shapes["unavailable"] += 1
                identities["unavailable"] += 1
                continue
            if symbol.kind in _TYPE_KINDS:
                producer_kinds["constructor"] += 1
                return_shapes["named"] += 1
                identities["repository_declared"] += 1
                continue
            producer_kinds["method"] += 1
            raw = declared_return_type(symbol.signature or "")
            if raw is None:
                return_shapes["missing"] += 1
                identities["unavailable"] += 1
                continue
            generic = "<" in raw and ">" in raw
            nullable = raw.rstrip().endswith("?")
            if generic and nullable:
                return_shapes["generic_nullable"] += 1
            elif generic:
                return_shapes["generic"] += 1
            elif nullable:
                return_shapes["nullable"] += 1
            else:
                return_shapes["named"] += 1
            normalized = normalize_return_type(raw, language)
            identities["repository_declared" if normalized in known_types else "external"] += 1
    for key in sorted(before.keys() | after.keys()):
        old = before.get(key)
        new = after.get(key)
        if old == new:
            continue
        if old is None:
            bucket = "added"
        elif new is None:
            bucket = "refused" if key in structural else "lost"
        elif old["callee"] != new["callee"]:
            bucket = "retargeted"
        else:
            # Origin/confidence-only movement is still explicit, but is not an
            # edge-identity change and must not inflate add/retarget counts.
            bucket = "metadata_only"
        counts[bucket] += 1
        path, line, target = key
        lines = source_lines.get(path, [])
        rows[bucket].append(
            {
                "file": path,
                "line": line,
                "target": target,
                "source": lines[line - 1].strip() if 0 < line <= len(lines) else "",
                "before": old,
                "after": new,
            }
        )
    return {
        "repo": str(repo.resolve()),
        "language": language,
        "files": len(parsed),
        "counts": dict(sorted(counts.items())),
        "shape_counts": {
            "producer_kind": dict(sorted(producer_kinds.items())),
            "return_shape": dict(sorted(return_shapes.items())),
            "identity": dict(sorted(identities.items())),
        },
        "rows": dict(sorted(rows.items())),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("repo", type=Path)
    parser.add_argument(
        "--language", required=True, choices=("java", "cpp", "csharp", "typescript")
    )
    parser.add_argument("--out", type=Path)
    parser.add_argument("--compare", action="store_true")
    args = parser.parse_args()
    result = (
        compare(args.repo.resolve(), args.language)
        if args.compare
        else measure(args.repo.resolve(), args.language)
    )
    payload = json.dumps(result, indent=2, sort_keys=True)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(payload + "\n", encoding="utf-8")
        summary_key = "counts" if args.compare else "buckets"
        print(json.dumps({**result, "rows": None, "examples": None}.get(summary_key, {})))
    else:
        print(payload)


if __name__ == "__main__":
    main()
