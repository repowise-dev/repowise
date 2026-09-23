"""Unit tests for ``is_chunked_loop`` on the Go and Java perf dialects.

Mirrors ``test_perf_loop_facts_ts.py``: goes through the real walker
(``walk_file``) so the tests also cover the sink-detection gate each hit
depends on (a hit only carries ``.loop`` facts when its call is first
classified as an I/O sink). Precision over recall: every negative asserts
``loop is None`` or ``not loop.chunked``, never a guess.
"""

from __future__ import annotations

from repowise.core.analysis.health.complexity import walk_file


def _io_hits(src: str, lang: str):
    fc = walk_file(f"f.{lang}", lang, src.encode())
    return [h for h in fc.perf_hits if h.kind == "io_in_loop"]


# ---------------------------------------------------------------------------
# Go
# ---------------------------------------------------------------------------


def test_go_counted_loop_with_non_unit_step_is_chunked():
    src = """
package sample
import "net/http"
func f(urls []string) {
	for i := 0; i < len(urls); i += 10 {
		http.Get(urls[i])
	}
}
"""
    hits = _io_hits(src, "go")
    assert len(hits) == 1
    assert hits[0].loop is not None and hits[0].loop.chunked is True


def test_go_counted_loop_with_named_step_is_chunked():
    src = """
package sample
import "net/http"
func f(urls []string, step int) {
	for i := 0; i < len(urls); i += step {
		http.Get(urls[i])
	}
}
"""
    hits = _io_hits(src, "go")
    assert len(hits) == 1
    assert hits[0].loop is not None and hits[0].loop.chunked is True


def test_go_range_over_slices_chunk_is_chunked():
    src = """
package sample
import "net/http"
func f(urls []string) {
	for _, chunk := range slices.Chunk(urls, 10) {
		http.Get(chunk[0])
	}
}
"""
    hits = _io_hits(src, "go")
    assert len(hits) == 1
    assert hits[0].loop is not None and hits[0].loop.chunked is True


def test_go_range_over_named_batch_helper_is_chunked():
    src = """
package sample
import "net/http"
func f(urls []string) {
	for _, b := range Batch(urls, 5) {
		http.Get(b[0])
	}
}
"""
    hits = _io_hits(src, "go")
    assert len(hits) == 1
    assert hits[0].loop is not None and hits[0].loop.chunked is True


def test_go_unit_increment_is_not_chunked():
    src = """
package sample
import "net/http"
func f(urls []string) {
	for i := 0; i < len(urls); i++ {
		http.Get(urls[i])
	}
}
"""
    hits = _io_hits(src, "go")
    assert len(hits) == 1
    assert hits[0].loop is None or not hits[0].loop.chunked


def test_go_literal_step_of_one_is_not_chunked():
    src = """
package sample
import "net/http"
func f(urls []string) {
	for i := 0; i < len(urls); i += 1 {
		http.Get(urls[i])
	}
}
"""
    hits = _io_hits(src, "go")
    assert len(hits) == 1
    assert hits[0].loop is None or not hits[0].loop.chunked


def test_go_countdown_is_not_chunked():
    # ``i >= 0`` (a literal condition bound) trips ``is_constant_loop`` and
    # suppresses the sink entirely, so the bound is a variable here to isolate
    # the ``is_chunked_loop`` predicate under test.
    src = """
package sample
import "net/http"
func f(urls []string, step int, lowest int) {
	for i := len(urls) - 1; i >= lowest; i -= step {
		http.Get(urls[i])
	}
}
"""
    hits = _io_hits(src, "go")
    assert len(hits) == 1
    assert hits[0].loop is None or not hits[0].loop.chunked


def test_go_range_over_plain_slice_is_not_chunked():
    src = """
package sample
import "net/http"
func f(urls []string) {
	for _, u := range urls {
		http.Get(u)
	}
}
"""
    hits = _io_hits(src, "go")
    assert len(hits) == 1
    assert hits[0].loop is None or not hits[0].loop.chunked


# ---------------------------------------------------------------------------
# Java
# ---------------------------------------------------------------------------


def test_java_counted_loop_with_non_unit_step_is_chunked():
    src = """
class C {
  void f(java.util.List<Integer> ids) {
    for (int i = 0; i < ids.size(); i += 10) {
      repo.findByIdIn(ids.get(i));
    }
  }
}
"""
    hits = _io_hits(src, "java")
    assert len(hits) == 1
    assert hits[0].loop is not None and hits[0].loop.chunked is True


def test_java_counted_loop_with_named_step_is_chunked():
    src = """
class C {
  void f(java.util.List<Integer> ids, int step) {
    for (int i = 0; i < ids.size(); i += step) {
      repo.findByIdIn(ids.get(i));
    }
  }
}
"""
    hits = _io_hits(src, "java")
    assert len(hits) == 1
    assert hits[0].loop is not None and hits[0].loop.chunked is True


def test_java_enhanced_for_over_lists_partition_is_chunked():
    src = """
class C {
  void f(java.util.List<Integer> ids) {
    for (java.util.List<Integer> chunk : Lists.partition(ids, 10)) {
      repo.findByIdIn(chunk);
    }
  }
}
"""
    hits = _io_hits(src, "java")
    assert len(hits) == 1
    assert hits[0].loop is not None and hits[0].loop.chunked is True


def test_java_enhanced_for_over_list_utils_partition_is_chunked():
    src = """
class C {
  void f(java.util.List<Integer> ids) {
    for (java.util.List<Integer> chunk : ListUtils.partition(ids, 10)) {
      repo.findByIdIn(chunk);
    }
  }
}
"""
    hits = _io_hits(src, "java")
    assert len(hits) == 1
    assert hits[0].loop is not None and hits[0].loop.chunked is True


def test_java_enhanced_for_over_named_partition_call_is_chunked():
    src = """
class C {
  void f(java.util.List<Integer> ids) {
    for (java.util.List<Integer> chunk : partition(ids, 10)) {
      repo.findByIdIn(chunk);
    }
  }
}
"""
    hits = _io_hits(src, "java")
    assert len(hits) == 1
    assert hits[0].loop is not None and hits[0].loop.chunked is True


def test_java_unit_increment_is_not_chunked():
    src = """
class C {
  void f(java.util.List<Integer> ids) {
    for (int i = 0; i < ids.size(); i++) {
      repo.findByIdIn(ids.get(i));
    }
  }
}
"""
    hits = _io_hits(src, "java")
    assert len(hits) == 1
    assert hits[0].loop is None or not hits[0].loop.chunked


def test_java_literal_step_of_one_is_not_chunked():
    src = """
class C {
  void f(java.util.List<Integer> ids) {
    for (int i = 0; i < ids.size(); i += 1) {
      repo.findByIdIn(ids.get(i));
    }
  }
}
"""
    hits = _io_hits(src, "java")
    assert len(hits) == 1
    assert hits[0].loop is None or not hits[0].loop.chunked


def test_java_countdown_is_not_chunked():
    src = """
class C {
  void f(java.util.List<Integer> ids, int step) {
    for (int i = ids.size() - 1; i >= 0; i -= step) {
      repo.findByIdIn(ids.get(i));
    }
  }
}
"""
    hits = _io_hits(src, "java")
    assert len(hits) == 1
    assert hits[0].loop is None or not hits[0].loop.chunked


def test_java_enhanced_for_over_plain_list_is_not_chunked():
    src = """
class C {
  void f(java.util.List<Integer> ids) {
    for (Integer id : ids) {
      repo.findByIdIn(id);
    }
  }
}
"""
    hits = _io_hits(src, "java")
    assert len(hits) == 1
    assert hits[0].loop is None or not hits[0].loop.chunked
