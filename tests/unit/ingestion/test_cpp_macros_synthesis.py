"""Unit tests for C/C++ registration-macro synthetic-symbol synthesis."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from repowise.core.ingestion.models import FileInfo
from repowise.core.ingestion.parser import parse_file


def _fi(rel: str, abs_: Path, lang: str = "cpp") -> FileInfo:
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


def _parse(tmp_path: Path, name: str, src: str, lang: str = "cpp"):
    f = tmp_path / name
    f.write_text(src)
    return parse_file(_fi(name, f, lang), src.encode("utf-8"))


class TestCppMacroSynthesis:
    def test_pybind11_module_emits_synthetic_module_symbol(
        self, tmp_path: Path
    ) -> None:
        src = """\
#include <pybind11/pybind11.h>
namespace py = pybind11;
int add(int a, int b) { return a + b; }
PYBIND11_MODULE(my_ext, m) {
  m.def("add", &add);
}
"""
        pf = _parse(tmp_path, "binding.cc", src)
        by_name = {s.name: s for s in pf.symbols}
        assert "my_ext" in by_name
        assert by_name["my_ext"].kind == "module"

    def test_boost_python_module_emits_synthetic_symbol(self, tmp_path: Path) -> None:
        src = """\
#include <boost/python.hpp>
BOOST_PYTHON_MODULE(legacy_ext) {
}
"""
        pf = _parse(tmp_path, "boost_binding.cc", src)
        names = {s.name for s in pf.symbols}
        assert "legacy_ext" in names

    def test_gflags_define_emits_FLAGS_prefixed_symbol(self, tmp_path: Path) -> None:
        src = """\
#include <gflags/gflags.h>
DEFINE_string(host, "localhost", "the host");
DEFINE_int32(port, 8080, "the port");
"""
        pf = _parse(tmp_path, "flags.cc", src)
        names = {s.name for s in pf.symbols}
        assert "FLAGS_host" in names
        assert "FLAGS_port" in names

    def test_absl_flag_emits_FLAGS_prefixed_symbol(self, tmp_path: Path) -> None:
        src = """\
#include <absl/flags/flag.h>
ABSL_FLAG(std::string, host, "localhost", "the host");
"""
        pf = _parse(tmp_path, "absl_flags.cc", src)
        names = {s.name for s in pf.symbols}
        assert "FLAGS_host" in names

    def test_no_macros_no_synthetic_symbols(self, tmp_path: Path) -> None:
        """Cheap reject path: source without any macro token emits nothing."""
        src = """\
int main() { return 0; }
"""
        pf = _parse(tmp_path, "plain.cc", src)
        # Only the real ``main`` symbol — no synthetic adds.
        synth_names = {s.name for s in pf.symbols if "PYBIND11" in (s.signature or "")
                       or "BOOST_PYTHON" in (s.signature or "")
                       or "ABSL_FLAG" in (s.signature or "")
                       or "DEFINE_*" in (s.signature or "")}
        assert synth_names == set()
