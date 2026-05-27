"""Phase 5A — JavaScript never-flag patterns for embedded/built assets."""

from __future__ import annotations

import fnmatch


def _matches(path: str) -> bool:
    from repowise.core.analysis.dead_code.constants import _NEVER_FLAG_PATTERNS
    return any(fnmatch.fnmatch(path, p) for p in _NEVER_FLAG_PATTERNS)


class TestJsNeverFlagPatterns:
    def test_bundle_outputs(self):
        assert _matches("internal/warpc/js/main.bundle.js")
        assert _matches("docs/assets/js/index.bundle.js")
        assert _matches("static/app.bundle.mjs")

    def test_minified(self):
        assert _matches("docs/static/js/vendor.min.js")

    def test_livereload_generated_and_shim(self):
        assert _matches("livereload/gen/Connector.js")
        assert _matches("transport/livereload/gen/Timer.js")
        assert _matches("livereload/livereload.js")

    def test_wasm_exec_glue(self):
        assert _matches("internal/warpc/js/wasm_exec.js")

    def test_ordinary_js_not_flagged(self):
        assert not _matches("docs/assets/js/explorer.js")
        assert not _matches("src/app.js")
