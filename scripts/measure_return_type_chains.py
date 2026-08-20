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


def _baseline(
    repo: Path, parsed: dict[str, ParsedFile], sources: dict[str, bytes]
) -> dict[tuple[str, int, str], str]:
    """Record legacy results while the production return-type lanes are off."""

    resolved: dict[tuple[str, int, str], str] = {}
    original = resolver_module.CallResolver._resolve_one
    old_languages = resolver_module.PRODUCTION_RETURN_TYPE_CHAIN_LANGUAGES
    resolver_module.PRODUCTION_RETURN_TYPE_CHAIN_LANGUAGES = frozenset()

    def recording(self, file_path, call):
        result = original(self, file_path, call)
        if result is not None:
            resolved.setdefault((file_path, call.line, call.target_name), result.callee_id)
        return result

    resolver_module.CallResolver._resolve_one = recording
    try:
        builder = GraphBuilder(repo)
        for parsed_file in parsed.values():
            builder.add_file(parsed_file)
        builder.set_source_map(sources)
        builder.build()
    finally:
        resolver_module.CallResolver._resolve_one = original
        resolver_module.PRODUCTION_RETURN_TYPE_CHAIN_LANGUAGES = old_languages
    return resolved


def measure(repo: Path, language: str) -> dict[str, object]:
    parsed, sources = _parse_repo(repo, language)
    baseline = _baseline(repo, parsed, sources)
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
            if call.receiver_call is None:
                continue
            key = (path, call.line, call.target_name)
            if key in seen:
                continue
            seen.add(key)
            buckets["structural_sites"] += 1
            inner = call.receiver_call
            inner_id = baseline.get((path, call.line, inner.target_name))
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
                    before = baseline.get(key)
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

    return {
        "repo": str(repo.resolve()),
        "language": language,
        "files": len(parsed),
        "buckets": dict(sorted(buckets.items())),
        "examples": dict(sorted(examples.items())),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("repo", type=Path)
    parser.add_argument(
        "--language", required=True, choices=("java", "cpp", "csharp", "typescript")
    )
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    result = measure(args.repo.resolve(), args.language)
    payload = json.dumps(result, indent=2, sort_keys=True)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(payload + "\n", encoding="utf-8")
    print(payload)


if __name__ == "__main__":
    main()
