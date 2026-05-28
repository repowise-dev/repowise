"""Tests for the Bazel BUILD reader."""

from __future__ import annotations

from pathlib import Path

from repowise.core.ingestion.external_systems import bazel


def _write(tmp_path: Path, rel: str, text: str) -> Path:
    p = tmp_path / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    return p


def test_cc_library_with_srcs_and_hdrs(tmp_path):
    _write(tmp_path, "lib/BUILD.bazel", """
cc_library(
    name = "strings",
    srcs = ["str.cc", "str_util.cc"],
    hdrs = ["str.h"],
    deps = ["//base:logging"],
    includes = ["."],
)
""")
    bf = bazel.parse_bazel_build(tmp_path / "lib/BUILD.bazel", repo_root=tmp_path)
    assert bf.package == "lib"
    assert len(bf.targets) == 1
    t = bf.targets[0]
    assert t.name == "strings"
    assert t.kind == "cc_library"
    assert "lib/str.cc" in t.srcs
    assert "lib/str.h" in t.hdrs
    assert "//base:logging" in t.deps


def test_cc_test_is_testonly(tmp_path):
    _write(tmp_path, "BUILD", """
cc_test(
    name = "str_test",
    srcs = ["str_test.cc"],
    deps = [":strings", "@gtest//:gtest_main"],
)
""")
    bf = bazel.parse_bazel_build(tmp_path / "BUILD", repo_root=tmp_path)
    assert bf.targets[0].testonly is True
    assert bf.targets[0].kind == "cc_test"


def test_label_resolution(tmp_path):
    _write(tmp_path, "src/foo/BUILD.bazel", """
cc_binary(
    name = "tool",
    srcs = ["main.cc", "//src/foo:helper.cc", ":local.cc"],
)
""")
    bf = bazel.parse_bazel_build(tmp_path / "src/foo/BUILD.bazel", repo_root=tmp_path)
    srcs = bf.targets[0].srcs
    assert "src/foo/main.cc" in srcs
    assert "src/foo/helper.cc" in srcs
    assert "src/foo/local.cc" in srcs


def test_is_bazel_repo(tmp_path):
    assert bazel.is_bazel_repo(tmp_path) is False
    (tmp_path / "MODULE.bazel").write_text("module(name='x')", encoding="utf-8")
    assert bazel.is_bazel_repo(tmp_path) is True


def test_malformed_does_not_raise(tmp_path):
    p = _write(tmp_path, "BUILD", "cc_library(name = ")
    bf = bazel.parse_bazel_build(p, repo_root=tmp_path)
    assert bf.targets == []
