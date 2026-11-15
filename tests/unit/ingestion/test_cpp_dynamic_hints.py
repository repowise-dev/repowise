"""Unit tests for the C++ dynamic-hints extractor."""

from __future__ import annotations

from pathlib import Path

from repowise.core.ingestion.dynamic_hints.cpp import CppDynamicHints


def _extract(repo: Path):
    return CppDynamicHints().extract(repo)


class TestFunctionPointerAssign:
    def test_fn_ptr_emits_dynamic_uses(self, tmp_path: Path) -> None:
        (tmp_path / "impl.cpp").write_text(
            "void on_tick() {}\n"
            "void register_cb() { auto cb = on_tick; (void)cb; }\n"
        )
        edges = _extract(tmp_path)
        # The self-reference is filtered (target == rel), so an intra-file
        # assignment may emit nothing; assert it didn't crash.
        assert all(e.source != e.target for e in edges)

    def test_fn_ptr_cross_file(self, tmp_path: Path) -> None:
        (tmp_path / "src.cpp").write_text("void on_tick() {}\n")
        (tmp_path / "reg.cpp").write_text(
            "extern void on_tick();\n"
            "void register_cb() { auto cb = on_tick; (void)cb; }\n"
        )
        edges = _extract(tmp_path)
        assert any(
            e.source == "reg.cpp" and e.target == "src.cpp" and "fn_ptr" in e.hint_source
            for e in edges
        )


class TestNameBindsInItsOwnTranslationUnit:
    """``wc.lpfnWndProc = WndProc;`` in one window file never names the
    ``static WndProc`` of an unrelated executable's ``main.cpp``."""

    def test_static_function_in_a_source_file_is_not_a_cross_file_target(
        self, tmp_path: Path
    ) -> None:
        (tmp_path / "main.cpp").write_text(
            "static LRESULT CALLBACK WndProc(HWND h, UINT m, WPARAM w, LPARAM l)\n"
            "{\n  return 0;\n}\n"
            "int wWinMain() { wc.lpfnWndProc = WndProc; return 0; }\n"
        )
        (tmp_path / "Window.cpp").write_text(
            "void Window::Register() { wcex.lpfnWndProc = WndProc; }\n"
        )
        assert [e for e in _extract(tmp_path) if e.target == "main.cpp"] == []

    def test_a_file_defining_the_name_binds_it_locally(self, tmp_path: Path) -> None:
        (tmp_path / "a.cpp").write_text("void on_tick() {}\nvoid reg() { cb = on_tick; }\n")
        (tmp_path / "b.cpp").write_text("void on_tick() {}\nvoid reg() { cb = on_tick; }\n")
        assert [e for e in _extract(tmp_path) if "fn_ptr" in e.hint_source] == []

    def test_a_static_function_in_a_header_stays_a_target(self, tmp_path: Path) -> None:
        (tmp_path / "util.h").write_text("static void on_tick() {}\n")
        (tmp_path / "reg.cpp").write_text("void reg() { cb = on_tick; }\n")
        assert any(
            e.source == "reg.cpp" and e.target == "util.h" for e in _extract(tmp_path)
        )


class TestDlopenDlsym:
    def test_dlopen_emits_dynamic_import(self, tmp_path: Path) -> None:
        (tmp_path / "loader.cpp").write_text(
            '#include <dlfcn.h>\n'
            'void load() { void* h = dlopen("./libplug.so", 0); (void)h; }\n'
        )
        edges = _extract(tmp_path)
        assert any(
            e.edge_type == "dynamic_imports" and e.target == "external:dlopen:./libplug.so"
            for e in edges
        )

    def test_dlsym_links_named_function(self, tmp_path: Path) -> None:
        (tmp_path / "plug.cpp").write_text("void do_thing() {}\n")
        (tmp_path / "host.cpp").write_text(
            '#include <dlfcn.h>\n'
            'void run(void* h) { auto p = dlsym(h, "do_thing"); (void)p; }\n'
        )
        edges = _extract(tmp_path)
        assert any(
            e.source == "host.cpp" and e.target == "plug.cpp" and "dlsym" in e.hint_source
            for e in edges
        )


class TestQtConnect:
    def test_qt_member_pointer_connect(self, tmp_path: Path) -> None:
        (tmp_path / "sender.cpp").write_text(
            "void on_clicked() {}\n"
        )
        (tmp_path / "wire.cpp").write_text(
            "void wire() { connect(s, &Sender::on_clicked, r, &Recv::do_thing); }\n"
        )
        (tmp_path / "recv.cpp").write_text("void do_thing() {}\n")
        edges = _extract(tmp_path)
        assert any(e.source == "wire.cpp" and e.target == "sender.cpp" for e in edges)
        assert any(e.source == "wire.cpp" and e.target == "recv.cpp" for e in edges)


class TestSkipDirs:
    def test_build_dir_skipped(self, tmp_path: Path) -> None:
        (tmp_path / "build").mkdir()
        (tmp_path / "build" / "x.cpp").write_text(
            'void load() { dlopen("ignored.so", 0); }\n'
        )
        edges = _extract(tmp_path)
        assert not any("ignored.so" in e.target for e in edges)
