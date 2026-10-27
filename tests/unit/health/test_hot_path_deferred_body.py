"""A sink inside a deferred body is not charged to the function that defines it.

A generator's body and an ``iter.Seq`` closure run when the caller iterates
them, not when the function is called, so a blocking read there is not a
per-call wait. A plain function that reads on every call stays a candidate.
"""

from __future__ import annotations

from repowise.core.analysis.health.complexity import walk_file


def _hot_sinks(lang: str, src: str) -> list[str]:
    fc = walk_file(f"t.{lang}", lang, src.encode())
    return [f.blocking_sink_kind for f in fc.perf_fn_facts if f.blocking_sink_kind]


class TestPythonGenerators:
    def test_a_generator_reading_a_file_is_not_a_hot_path_candidate(self) -> None:
        src = (
            "def iter_lines(path):\n"
            "    with open(path) as fh:\n"
            "        for line in fh:\n"
            "            yield line.rstrip('\\n')\n"
        )
        assert _hot_sinks("python", src) == []

    def test_a_plain_function_reading_the_whole_file_is_still_a_candidate(self) -> None:
        src = "def read_all(path):\n    return open(path).read()\n"
        assert _hot_sinks("python", src) == ["filesystem"]

    def test_a_read_before_the_first_yield_is_deferred_too(self) -> None:
        src = "def lines(path):\n    cfg = open(path).read()\n    yield cfg\n"
        assert _hot_sinks("python", src) == []

    def test_a_nested_plain_function_does_not_inherit_the_generator_exemption(self) -> None:
        src = "def outer(path):\n    def gen():\n        yield 1\n    return open(path).read()\n"
        assert _hot_sinks("python", src) == ["filesystem"]


class TestGoIteratorClosures:
    def test_an_iter_seq_closure_reading_a_file_is_not_a_hot_path_candidate(self) -> None:
        src = (
            "package gopkg\n"
            'import (\n\t"bufio"\n\t"iter"\n\t"os"\n)\n'
            "func Lines(path string) iter.Seq[string] {\n"
            "\treturn func(yield func(string) bool) {\n"
            "\t\tf, err := os.Open(path)\n"
            "\t\tif err != nil {\n\t\t\treturn\n\t\t}\n"
            "\t\tdefer f.Close()\n"
            "\t\ts := bufio.NewScanner(f)\n"
            "\t\tfor s.Scan() {\n\t\t\tif !yield(s.Text()) {\n\t\t\t\treturn\n\t\t\t}\n\t\t}\n"
            "\t}\n"
            "}\n"
        )
        assert _hot_sinks("go", src) == []

    def test_a_plain_go_function_reading_a_file_is_still_a_candidate(self) -> None:
        src = (
            'package gopkg\nimport "os"\n'
            "func ReadAll(path string) []byte {\n"
            "\tb, _ := os.ReadFile(path)\n"
            "\treturn b\n"
            "}\n"
        )
        assert _hot_sinks("go", src) == ["filesystem"]
