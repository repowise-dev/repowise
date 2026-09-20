"""The conventions source: majority import patterns proposed from the graph.

Each hazard has an ablation partner, and no count ever makes a record active.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from repowise.core.analysis.decisions.conventions import (
    MAX_PROPOSALS,
    MIN_RATIO,
    MIN_THROUGH,
    scan_conventions,
)
from repowise.core.analysis.decisions.extractor import DecisionExtractor
from repowise.core.analysis.decisions.gate import apply_substring_gate
from repowise.core.ingestion.graph import GraphBuilder
from repowise.core.ingestion.models import FileInfo
from repowise.core.ingestion.parser import parse_file
from repowise.server.mcp_server._helpers import _compute_alignment

WRAPPER = "net/client.py"
WRAPPER_SRC = "import httpx\n\n\ndef get(url):\n    return httpx.get(url)\n\n\ndef other_helper():\n    return 1\n"
DIRECT_SRC = "import httpx\n\n\ndef raw(url):\n    return httpx.get(url)\n"


def _language(path: str) -> str:
    return {"py": "python", "go": "go", "ts": "typescript", "mod": "go"}[path.rsplit(".", 1)[1]]


def _build(tmp_path: Path, files: dict[str, str]):
    """Write *files*, parse them with the real parser, and build the graph."""
    parsed_files = []
    source_map: dict[str, bytes] = {}
    for rel, src in files.items():
        target = tmp_path / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(src, encoding="utf-8")
        raw = src.encode("utf-8")
        info = FileInfo(
            path=rel,
            abs_path=str(target),
            language=_language(rel),
            size_bytes=len(raw),
            git_hash="",
            last_modified=datetime.now(),
            is_test=rel.startswith("tests/"),
            is_config=False,
            is_api_contract=False,
            is_entry_point=False,
        )
        parsed_files.append(parse_file(info, raw))
        source_map[rel] = raw
    builder = GraphBuilder(repo_path=tmp_path)
    for pf in parsed_files:
        builder.add_file(pf)
    builder.build()
    return builder.graph(), parsed_files, source_map


def _repo(through: int = 6, direct: int = 1, **extra: str) -> dict[str, str]:
    files = {"net/__init__.py": "", "svc/__init__.py": "", WRAPPER: WRAPPER_SRC}
    for i in range(through):
        files[f"svc/user{i}.py"] = f"from net.client import get\n\n\ndef load{i}():\n    return get('u')\n"
    for i in range(direct):
        files[f"svc/raw{i}.py"] = DIRECT_SRC
    files.update(extra)
    return files


GO_CLIENT = '''package client

import "net/http"

func Get(u string) (*http.Response, error) {
	return http.Get(u)
}

func Other() int {
	return 1
}
'''

GO_CALLER = '''package svc

import "example.com/x/client"

func F() {
	client.Get("u")
}
'''

GO_RAW = '''package raw

import "net/http"

func R() {
	http.Get("u")
}
'''


def _go_repo(callers: int, direct: int) -> dict[str, str]:
    files = {"go.mod": "module example.com/x\n\ngo 1.22\n", "client/client.go": GO_CLIENT}
    for i in range(callers):
        files[f"svc{i}/main.go"] = GO_CALLER
    for i in range(direct):
        files[f"raw{i or ''}/raw.go"] = GO_RAW
    return files


def _scan(tmp_path: Path, files: dict[str, str], **kw):
    graph, parsed, source_map = _build(tmp_path, files)
    return scan_conventions(graph, parsed, source_map, tmp_path, **kw)


class TestOneWrapperOneLibrary:
    def test_proposes_exactly_one_candidate_with_the_counts(self, tmp_path):
        out = _scan(tmp_path, _repo(through=6, direct=1))

        assert len(out) == 1
        d = out[0]
        assert d.title == f"httpx goes through {WRAPPER}"
        assert d.decision == f"6 of 7 files reach httpx through {WRAPPER}; 1 import it directly."
        assert d.consequences == ["svc/raw0.py imports httpx directly"]
        assert d.affected_files == [WRAPPER, "svc/raw0.py"]
        assert d.affected_modules == ["net", "svc"]
        assert d.evidence_file == WRAPPER
        assert d.evidence_line == 4
        assert d.tags == ["convention", "network"]
        assert d.source == "conventions"
        assert d.status == "proposed"
        assert d.staleness_score == pytest.approx(1 / 7, abs=1e-3)
        assert f"Wrapper symbol {WRAPPER}::get at {WRAPPER}:4" in d.context
        assert "svc/user0.py:5" in d.context

    def test_the_gate_reads_the_counts_as_exact(self, tmp_path):
        kept, rejected = apply_substring_gate(_scan(tmp_path, _repo()))

        assert rejected == 0
        assert kept[0].verification == "exact"
        assert kept[0].source_text == ""
        assert kept[0].source_quote.startswith("6 of 7 files reach httpx")

    def test_no_direct_importers_is_a_clean_majority(self, tmp_path):
        out = _scan(tmp_path, _repo(through=6, direct=0))

        assert len(out) == 1
        assert out[0].decision.endswith("; 0 import it directly.")
        assert out[0].consequences == []
        assert out[0].staleness_score == 0.0


class TestThresholds:
    def test_below_min_through_emits_nothing(self, tmp_path):
        assert _scan(tmp_path, _repo(through=MIN_THROUGH - 1, direct=0)) == []

    def test_at_min_through_emits(self, tmp_path):
        assert len(_scan(tmp_path, _repo(through=MIN_THROUGH, direct=0))) == 1

    def test_too_many_direct_importers_emits_nothing(self, tmp_path):
        through = MIN_RATIO * 2
        assert _scan(tmp_path, _repo(through=through, direct=3)) == []

    def test_direct_importers_at_the_ratio_still_emit(self, tmp_path):
        through = MIN_RATIO * 2
        assert len(_scan(tmp_path, _repo(through=through, direct=2))) == 1

    def test_the_cap_holds_and_orders_by_reach(self, tmp_path):
        files = _repo(through=6, direct=0)
        files["os/runner.py"] = "import subprocess\n\n\ndef run(cmd):\n    return subprocess.run(cmd)\n"
        for i in range(8):
            files[f"jobs/job{i}.py"] = "from os.runner import run\n\n\ndef go():\n    return run('x')\n"
        files["jobs/__init__.py"] = ""
        files["os/__init__.py"] = ""

        out = _scan(tmp_path, files)
        assert [d.title.split(" goes")[0] for d in out] == ["subprocess", "httpx"]
        assert len(_scan(tmp_path, files, limit=1)) == 1
        assert MAX_PROPOSALS >= 2


class TestHazards:
    def test_test_files_count_on_neither_side(self, tmp_path):
        files = _repo(through=6, direct=1)
        files["tests/test_client.py"] = "import httpx\nfrom net.client import get\n\n\ndef test_it():\n    assert httpx and get\n"

        out = _scan(tmp_path, files)
        assert out[0].decision.startswith("6 of 7 files")

    def test_the_wrapper_own_file_is_not_a_direct_importer(self, tmp_path):
        out = _scan(tmp_path, _repo(through=6, direct=0))

        assert out[0].decision.endswith("; 0 import it directly.")

    def test_two_confirmed_wrappers_emit_nothing(self, tmp_path):
        files = _repo(through=6, direct=0)
        files["net/client2.py"] = WRAPPER_SRC
        for i in range(6):
            files[f"svc/second{i}.py"] = "from net.client2 import get\n\n\ndef f():\n    return get('u')\n"

        assert _scan(tmp_path, files) == []

    def test_co_location_is_not_wrapping(self, tmp_path):
        files = _repo(through=6, direct=0)
        # The library is imported and used at module level, but no callable
        # in the file reaches it, so importing the file is not reaching httpx.
        files[WRAPPER] = "import httpx\n\nTIMEOUT = httpx.Timeout(5)\n\n\ndef get(url):\n    return url\n"

        assert _scan(tmp_path, files) == []

    def test_a_reference_inside_a_string_or_comment_does_not_confirm(self, tmp_path):
        files = _repo(through=6, direct=0)
        files[WRAPPER] = (
            "import httpx\n\n\ndef get(url):\n    # httpx.get(url) used to live here\n"
            "    return 'httpx.get(' + url\n"
        )

        assert _scan(tmp_path, files) == []

    def test_importing_the_file_for_another_helper_is_not_reaching(self, tmp_path):
        files = _repo(through=0, direct=0)
        for i in range(6):
            files[f"svc/helper{i}.py"] = "from net.client import other_helper\n\n\ndef f():\n    return other_helper()\n"

        assert _scan(tmp_path, files) == []

    def test_importing_the_module_whole_does_reach(self, tmp_path):
        files = _repo(through=0, direct=0)
        for i in range(6):
            files[f"svc/whole{i}.py"] = "from net import client\n\n\ndef f():\n    return client.get('u')\n"

        out = _scan(tmp_path, files)
        assert len(out) == 1
        assert out[0].decision.startswith("6 of 6 files")

    def test_an_untyped_library_emits_nothing(self, tmp_path):
        files = _repo(through=6, direct=0)
        files[WRAPPER] = "import somelib\n\n\ndef get(url):\n    return somelib.get(url)\n"

        assert _scan(tmp_path, files) == []

    def test_a_barrel_reexport_is_neither_wrapper_nor_direct(self, tmp_path):
        graph, parsed, source_map = _build(tmp_path, _repo(through=6, direct=1))
        # Mark the direct importer's statement as a re-export: the name is
        # forwarded, not used, so it drops out of the direct count.
        raw = next(pf for pf in parsed if pf.file_info.path == "svc/raw0.py")
        raw.imports[0].is_reexport = True

        out = scan_conventions(graph, parsed, source_map, tmp_path)
        assert out[0].decision.endswith("; 0 import it directly.")

    def test_a_go_wrapper_is_counted_by_package(self, tmp_path):
        files = _go_repo(callers=6, direct=1)

        out = _scan(tmp_path, files)
        assert len(out) == 1
        d = out[0]
        assert d.title == "net/http goes through client/client.go"
        assert d.decision == "6 of 7 packages reach net/http through client/client.go; 1 import it directly."
        assert d.consequences == ["raw imports net/http directly"]
        assert d.affected_files == ["client/client.go", "raw/raw.go"]
        assert "call edges" in d.rationale
        assert "svc0/main.go:" in d.context

    def test_a_go_package_importing_the_wrapper_without_calling_it_does_not_reach(self, tmp_path):
        files = _go_repo(callers=0, direct=0)
        for i in range(6):
            files[f"svc{i}/main.go"] = GO_CALLER.replace("client.Get(\"u\")", "client.Other()")

        assert _scan(tmp_path, files) == []

    def test_two_go_files_in_one_package_are_one_wrapper(self, tmp_path):
        files = _go_repo(callers=6, direct=0)
        files["client/more.go"] = GO_CLIENT.replace("Get(u string) (*http.Response, error)", "Post(u string) (*http.Response, error)").replace("http.Get(u)", "http.Post(u, \"\", nil)")

        out = _scan(tmp_path, files)
        assert len(out) == 1
        assert out[0].decision.startswith("6 of 6 packages")


class TestOnlyAPersonAccepts:
    @pytest.mark.parametrize("through", [MIN_THROUGH, 50, 200])
    def test_no_count_produces_an_active_record(self, tmp_path, through):
        out = _scan(tmp_path, _repo(through=through, direct=0))

        assert out and all(d.status == "proposed" for d in out)

    def test_a_conventions_candidate_governs_nothing_until_accepted(self):
        result = _compute_alignment(
            WRAPPER, [{"id": "c1", "title": f"httpx goes through {WRAPPER}"}], [], {}
        )

        assert result["active_count"] == 0
        assert result["candidate_count"] == 1


class TestExtractorWiring:
    async def test_extract_all_runs_the_source_and_gates_it(self, tmp_path):
        graph, parsed, source_map = _build(tmp_path, _repo())
        ex = DecisionExtractor(
            repo_path=tmp_path, graph=graph, parsed_files=parsed, source_map=source_map
        )

        report = await ex.extract_all(enabled_sources=["conventions"])

        assert report.by_source == {"conventions": 1}
        assert report.decisions[0].verification == "exact"

    async def test_no_graph_means_no_candidates(self, tmp_path):
        assert await DecisionExtractor(repo_path=tmp_path).scan_conventions() == []


class TestBoundInstance:
    """The second wrapper shape: a client built once at module level."""

    TS_WRAPPER = 'import axios from "axios";\n\nexport const service = axios.create({ baseURL: "/api" });\n\nexport const TIMEOUT = 5000;\n'

    def _ts_repo(self, users: int, direct: int = 0, readers: int = 0) -> dict[str, str]:
        files = {"src/utils/request.ts": self.TS_WRAPPER}
        for i in range(users):
            files[f"src/api/user{i}.ts"] = 'import { service } from "../utils/request";\n\nexport function load() {\n  return service.get("/u");\n}\n'
        for i in range(direct):
            files[f"src/api/raw{i}.ts"] = 'import axios from "axios";\n\nexport function raw() {\n  return axios.get("/u");\n}\n'
        for i in range(readers):
            files[f"src/api/reader{i}.ts"] = 'import { TIMEOUT } from "../utils/request";\n\nexport function wait() {\n  return TIMEOUT * 2;\n}\n'
        return files

    def test_a_called_instance_is_a_wrapper(self, tmp_path):
        out = _scan(tmp_path, self._ts_repo(users=6, direct=1))

        assert len(out) == 1
        d = out[0]
        assert d.title == "axios goes through src/utils/request.ts"
        assert d.decision.startswith("6 of 7 files reach axios")
        assert d.consequences == ["src/api/raw0.ts imports axios directly"]
        assert "Wrapper instance src/utils/request.ts::service" in d.context
        assert "src/api/user0.ts:4" in d.context

    def test_a_setting_that_is_only_read_is_not_a_wrapper(self, tmp_path):
        # Six files import TIMEOUT and read it; nothing calls through it.
        files = self._ts_repo(users=0, readers=6)
        files["src/utils/request.ts"] = 'import axios from "axios";\n\nexport const TIMEOUT = axios.defaults.timeout;\n'

        assert _scan(tmp_path, files) == []

    def test_readers_do_not_count_toward_the_instance(self, tmp_path):
        out = _scan(tmp_path, self._ts_repo(users=6, readers=6))

        assert len(out) == 1
        assert out[0].decision.startswith("6 of 6 files")

    def test_a_python_client_instance_is_a_wrapper(self, tmp_path):
        files = {
            "net/__init__.py": "",
            "svc/__init__.py": "",
            "net/client.py": "import httpx\n\nclient = httpx.Client(base_url='http://x')\n",
        }
        for i in range(6):
            files[f"svc/user{i}.py"] = "from net.client import client\n\n\ndef load():\n    return client.get('/u')\n"

        out = _scan(tmp_path, files)
        assert len(out) == 1
        assert "Wrapper instance net/client.py::client" in out[0].context
        assert out[0].evidence_line == 3
