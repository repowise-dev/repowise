from datetime import datetime
from pathlib import Path

from repowise.core.ingestion.call_resolver import CallResolver
from repowise.core.ingestion.models import FileInfo, ParsedFile
from repowise.core.ingestion.parser import parse_file


def _file_info(rel: str, abs_: Path, lang: str) -> FileInfo:
    return FileInfo(
        path=rel,
        abs_path=str(abs_),
        language=lang,
        size_bytes=abs_.stat().st_size,
        git_hash="",
        last_modified=datetime.now(),
        is_test=False,
        is_config=False,
        is_api_contract=False,
        is_entry_point=False,
    )


def _parse_all(tmp_path: Path, files: dict[str, tuple[str, str]]) -> dict[str, ParsedFile]:
    out = {}
    for rel, (lang, content) in files.items():
        abs_ = tmp_path / rel
        abs_.parent.mkdir(parents=True, exist_ok=True)
        abs_.write_text(content)
        fi = _file_info(rel, abs_, lang)
        out[rel] = parse_file(fi, content.encode("utf-8"))
    return out


def _resolve(parsed, tmp_path, import_targets):
    resolver = CallResolver(parsed, import_targets, repo_path=str(tmp_path))
    edges = []
    for path, pf in parsed.items():
        for rc in resolver.resolve_file(path, pf.calls):
            edges.append((rc.caller_id, rc.callee_id, rc.confidence, rc.origin))
    return edges


def test_python_submodule_resolution(tmp_path: Path):
    files = {
        "pkg/__init__.py": ("python", ""),
        "pkg/submod.py": ("python", "def helper():\n    return 1\n"),
        "caller.py": ("python", "from pkg import submod\n\ndef run():\n    return submod.helper()\n"),
    }
    parsed = _parse_all(tmp_path, files)
    
    # Set resolved_file so CallResolver's _build_import_maps works
    caller_pf = parsed["caller.py"]
    for imp in caller_pf.imports:
        imp.resolved_file = "pkg/__init__.py"
    
    # We provide import targets exactly as if it was found by the ts/js or python import resolver
    edges = _resolve(parsed, tmp_path, {
        "caller.py": {"pkg/__init__.py", "pkg/submod.py"},
        "pkg/__init__.py": set(),
        "pkg/submod.py": set()
    })
    
    edge_triples = [(c1, c2, conf) for c1, c2, conf, *_ in edges]
    assert ("caller.py::run", "pkg/submod.py::helper", 0.88) in edge_triples, edge_triples
