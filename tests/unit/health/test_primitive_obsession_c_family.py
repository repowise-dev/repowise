"""primitive_obsession in C and C++: indirection is not a raw value.

A pointer, an array or a mutable reference is memory the caller lends or an
out-parameter, and the integer right after a buffer is its length. A const
character pointer stays a string. SAL annotations (``_In_``) are not
parameters of their own.
"""

from __future__ import annotations

import pytest

from repowise.core.analysis.health.complexity import walk_file_complexity


def _fn(language: str, source: str):
    [fn] = walk_file_complexity(f"src/x.{language}", language, source.encode())
    return fn


@pytest.mark.parametrize("language", ["c", "cpp"])
def test_buffers_and_their_lengths_are_not_scalars(language: str) -> None:
    fn = _fn(
        language,
        "void blit(uint8_t *dst, size_t dst_len, const uint8_t *src, size_t src_len, int stride) {}\n",
    )
    assert (fn.param_count, fn.typed_param_count, fn.primitive_param_count) == (5, 5, 1)


@pytest.mark.parametrize("language", ["c", "cpp"])
def test_out_parameters_are_not_scalars(language: str) -> None:
    fn = _fn(language, "void bounds(const Shape *s, int *x, int *y, int *w, int *h) {}\n")
    assert fn.primitive_param_count == 0


@pytest.mark.parametrize("language", ["c", "cpp"])
def test_a_char_pointer_is_still_a_string(language: str) -> None:
    fn = _fn(language, "void draw(const char *label, char *path, int x, int y, int w) {}\n")
    assert fn.primitive_param_count == 5


@pytest.mark.parametrize("language", ["c", "cpp"])
def test_a_char_pointer_with_its_length_is_a_buffer(language: str) -> None:
    fn = _fn(language, "int put(int fd, char *buf, size_t len, int flags, int mode) {}\n")
    assert fn.primitive_param_count == 3


@pytest.mark.parametrize("language", ["c", "cpp"])
def test_an_object_pointer_lends_no_length_to_the_next_integer(language: str) -> None:
    fn = _fn(language, "void move(Tty *tty, int count, int x, int y, int w) {}\n")
    assert fn.primitive_param_count == 4


def test_cpp_references_mutable_ones_write_back() -> None:
    fn = _fn(
        "cpp",
        "void f(const std::string &name, std::string &out, int &count, std::string &&sink, int n) {}\n",
    )
    # name and sink are values, n is a scalar; out and count write back.
    assert fn.primitive_param_count == 3


def test_cpp_sal_annotations_are_not_parameters() -> None:
    fn = _fn(
        "cpp",
        "HRESULT C::GetCommandString(UINT_PTR idCmd, UINT uType, _In_ UINT* /*pReserved*/,"
        " LPSTR pszName, UINT cchMax)\n{\n    return S_OK;\n}\n",
    )
    assert fn.param_count == 5


def test_cpp_sal_annotated_named_parameters_count_once() -> None:
    fn = _fn("cpp", "void f(_In_ int a, _Out_ int* b, _Out_writes_(n) LPWSTR buf, size_t n) {}\n")
    assert fn.param_count == 4
