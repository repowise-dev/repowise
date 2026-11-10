"""A run of plain early-exit guards counts once in the CCN the complexity markers judge.

``if (!buf) return -1;``, Ruby ``return x if done`` and Swift
``guard ok else { return }`` each check one precondition and leave. Several
in a row read as one list of preconditions, not one decision each. Only a
guard with one condition, no ``else`` and a body that leaves qualifies.
"""

from __future__ import annotations

import pytest

from repowise.core.analysis.health.complexity import walk_file
from repowise.core.analysis.health.complexity.dispatch import judged_ccn

_EXT = {
    "ruby": "rb",
    "swift": "swift",
    "c": "c",
    "cpp": "cpp",
    "javascript": "js",
    "typescript": "ts",
    "java": "java",
    "csharp": "cs",
    "python": "py",
    "php": "php",
    "go": "go",
}


def _fn(language: str, source: str, name: str):
    fcx = walk_file(f"/tmp/sample.{_EXT[language]}", language, source.encode())
    if not fcx.functions:
        pytest.skip(f"{language} grammar unavailable")
    return {fc.name: fc for fc in fcx.functions}[name]


_RUBY = """
def filter_applies?(key, value, meta)
  silence do
    return located?(value) if key == :locations
    # ids are matched by position
    return by_id?(value) if key == :ids
    return nested?(value) if Hash === value

    meta_value = meta.fetch(key) { return false }

    raise ArgumentError, "no value" unless meta_value
    next if meta_value.nil?
    value === meta_value
  end
end

def mixed(a, b)
  return 1 if a && b
  return 2 if a
  if b
    return 3
  else
    log(b)
  end
  return 4 unless b
end
"""


def test_ruby_modifier_guard_runs_count_once() -> None:
    fn = _fn("ruby", _RUBY, "filter_applies?")
    # One run of five: a comment and a plain assignment do not end it.
    assert fn.exit_guards == 4
    assert judged_ccn(fn) == fn.ccn - 4


def test_ruby_two_conditions_or_an_else_are_not_guards() -> None:
    assert _fn("ruby", _RUBY, "mixed").exit_guards == 0


_SWIFT = """
func append(_ url: URL, name: String) {
    let headers = contentHeaders(name)

    // Check 1
    guard url.isFileURL else {
        setError(.invalid(url))
        return
    }

    // Check 2
    guard isReachable(url) else {
        setError(.unreachable(url))
        return
    }
    guard let size = fileSize(url) else { return }
    guard size > 0 else { throw E.empty }
    use(headers, size)
}

func pick(_ a: Int?, _ b: Int?) {
    guard let a = a, let b = b else { return }
    guard a > b else { return }
    if a > 2 { return } else { log(a) }
    guard b > 1 else {
        log(a)
        log(b)
        return
    }
}
"""


def test_swift_guard_run_counts_once() -> None:
    fn = _fn("swift", _SWIFT, "append")
    assert fn.exit_guards == 3
    assert judged_ccn(fn) == fn.ccn - 3


def test_swift_multi_condition_else_or_long_guards_do_not_count() -> None:
    assert _fn("swift", _SWIFT, "pick").exit_guards == 0


_C = """
int open_pipe(struct pipe *p, int fd) {
    int mode;
    int err;

    if (exists(p->loop, fd))
        return EEXIST;

    mode = fcntl(fd, F_GETFL);
    if (mode == -1)
        return errno; /* must be EBADF */

    err = nonblock(fd, 1);
    if (err)
        return err;
    if (!p->ready) { log_it(p); return EAGAIN; }

    if (mode != O_WRONLY)
        p->flags |= READABLE;
    if (mode != O_RDONLY)
        p->flags |= WRITABLE;
    return stream_open(p, fd);
}
"""


def test_c_plain_return_guard_run_counts_once() -> None:
    fn = _fn("c", _C, "open_pipe")
    assert fn.ccn == 7
    assert fn.exit_guards == 3
    assert judged_ccn(fn) == 4


_JS = """
function add(type, opts, fn) {
  if (typeof fn !== 'function') {
    throw new InvalidHandler()
  }
  if (this.exists(type)) {
    throw new AlreadyPresent(type)
  }
  if (!opts) return
  for (const t of list) {
    if (t.skip) continue
    if (t.done) break
    use(t)
  }
}
"""


def test_javascript_throw_and_loop_filter_runs_count_once() -> None:
    fn = _fn("javascript", _JS, "add")
    # Three throws/returns in a row, then ``continue`` / ``break`` in the loop.
    assert fn.exit_guards == 3
    assert judged_ccn(fn) == fn.ccn - 3


_GUARDS = {
    "python": (
        "def check(a, b, c):\n"
        "    if a is None:\n        return 0\n"
        "    if not b:\n        raise ValueError('b')\n"
        "    if c < 0:\n        return -1\n"
        "    return a + b + c\n",
        "check",
    ),
    "php": (
        "<?php\nfunction check($a, $b, $c) {\n"
        "  if ($a === null) return 0;\n"
        "  if (!$b) { throw new InvalidArgumentException('b'); }\n"
        "  if ($c < 0) return -1;\n"
        "  return $a + $b + $c;\n}\n",
        "check",
    ),
    "java": (
        "class K { int check(Integer a, int b, int c) {\n"
        "  if (a == null) return 0;\n"
        "  if (b == 0) { throw new IllegalArgumentException(); }\n"
        "  if (c < 0) return -1;\n"
        "  return a + b + c;\n} }\n",
        "check",
    ),
    "csharp": (
        "class K { int Check(int? a, int b, int c) {\n"
        "  if (a == null) return 0;\n"
        "  if (b == 0) throw new ArgumentException();\n"
        "  if (c < 0) return -1;\n"
        "  return a.Value + b + c;\n} }\n",
        "Check",
    ),
    "cpp": (
        "int check(int* a, int b, int c) {\n"
        "  if (!a) return 0;\n"
        '  if (b == 0) throw std::invalid_argument("b");\n'
        "  if (c < 0) return -1;\n"
        "  return *a + b + c;\n}\n",
        "check",
    ),
    "typescript": (
        "function check(a?: number, b?: number, c = 0): number {\n"
        "  if (a === undefined) return 0;\n"
        "  if (!b) { throw new Error('b'); }\n"
        "  if (c < 0) return -1;\n"
        "  return a + b + c;\n}\n",
        "check",
    ),
}


@pytest.mark.parametrize("language", sorted(_GUARDS))
def test_three_plain_guards_count_once(language: str) -> None:
    source, name = _GUARDS[language]
    fn = _fn(language, source, name)
    assert fn.ccn == 4
    assert fn.exit_guards == 2
    assert judged_ccn(fn) == 2


_CSHARP_YIELD = """
class K { IEnumerable<int> Pick(int a) {
  if (a > 1) yield return 1;
  if (a > 2) yield return 2;
  if (a > 3) yield return 3;
} }
"""


def test_csharp_yield_return_is_not_an_exit() -> None:
    assert _fn("csharp", _CSHARP_YIELD, "Pick").exit_guards == 0


_GO = """
package p

func first(a, b, c *T) *T {
	if a != nil {
		return a
	}
	if b != nil {
		return b
	}
	if c != nil {
		return c
	}
	return nil
}
"""


def test_go_keeps_only_its_error_guards() -> None:
    assert _fn("go", _GO, "first").exit_guards == 0
