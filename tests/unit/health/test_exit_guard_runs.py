"""A run of error guards counts once in the CCN the complexity markers judge.

Go ``if err != nil { return err }``, C ``if (rc) goto cleanup;`` and shell
``cmd || die`` hand an error up and stop. Ten of them in a row are one error
path, not ten decisions; the stored CCN is unchanged.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from repowise.core.analysis.health.complexity import walk_file
from repowise.core.analysis.health.complexity.dispatch import judged_ccn, judged_leaves_out

_EXT = {"go": "go", "c": "c", "shell": "sh"}


def _fn(language: str, source: str, name: str):
    fcx = walk_file(f"/tmp/sample.{_EXT[language]}", language, source.encode())
    if not fcx.functions:
        pytest.skip(f"{language} grammar unavailable")
    return {fc.name: fc for fc in fcx.functions}[name]


_GO = """
package p

func load(path string) (*Config, error) {
	f, err := os.Open(path)
	if err != nil {
		return nil, err
	}
	defer f.Close()
	raw, err := io.ReadAll(f)
	if err != nil {
		return nil, fmt.Errorf("read %s: %w", path, err)
	}
	var c Config
	if err := json.Unmarshal(raw, &c); err != nil {
		return nil, err
	}
	if writeErr := save(c); writeErr != nil {
		return nil, writeErr
	}
	return &c, nil
}

func split(xs []int) error {
	a, err := f()
	if err != nil {
		return err
	}
	for _, x := range xs {
		use(x)
	}
	b, err := g(a)
	if err != nil {
		return err
	}
	return use(b)
}

func notErrors(a, b *T) *T {
	if a != nil {
		return a
	}
	if b != nil {
		return b
	}
	if err != nil {
		log(err)
	}
	if err != nil && retry {
		return nil
	}
	return nil
}
"""


def test_go_error_guard_run_counts_once() -> None:
    fn = _fn("go", _GO, "load")
    assert fn.ccn == 5
    assert fn.exit_guards == 3
    assert judged_ccn(fn) == 2


def test_go_a_branch_between_guards_starts_a_new_run() -> None:
    fn = _fn("go", _GO, "split")
    assert fn.exit_guards == 0
    assert judged_ccn(fn) == fn.ccn


def test_go_guards_that_do_not_hand_an_error_up_still_count() -> None:
    fn = _fn("go", _GO, "notErrors")
    assert fn.exit_guards == 0
    assert judged_ccn(fn) == fn.ccn


_C = """
int setup(struct ctx *c) {
    int rc;
    rc = open_dev(c);
    if (rc) goto out;
    rc = map_regs(c);
    if (rc < 0)
        goto close;
    rc = start(c);
    if (rc) {
        log_err(c);
        goto unmap;
    }
    return 0;
unmap:
    unmap_regs(c);
close:
    close_dev(c);
out:
    return rc;
}

int pick(int a) {
    if (a) goto one;
    if (a > 2) { if (a > 3) goto two; }
    if (a > 4) goto one; else return 3;
one:
    return 1;
two:
    return 2;
}
"""


def test_c_goto_cleanup_run_counts_once() -> None:
    fn = _fn("c", _C, "setup")
    assert fn.ccn == 4
    assert fn.exit_guards == 2
    assert judged_ccn(fn) == 2


def test_c_nested_or_else_branches_are_not_guards() -> None:
    fn = _fn("c", _C, "pick")
    assert fn.exit_guards == 0


_SH = """
deploy() {
  cd "$dir" || exit 1
  git fetch origin || die "fetch failed"
  [ -f "$conf" ] || { echo "no config"; exit 2; }
  make build && make test
  run_it || return 1
}
"""


def test_shell_or_die_run_counts_once() -> None:
    fn = _fn("shell", _SH, "deploy")
    assert fn.ccn == 6
    # ``make build && make test`` is a decision of its own and ends the run.
    assert fn.exit_guards == 2
    assert judged_ccn(fn) == 4


def test_finding_text_names_the_cut_that_applied() -> None:
    guards = SimpleNamespace(ccn=12, exit_guards=4, dispatch_share=0.0, dispatch_arm=0)
    dispatch = SimpleNamespace(ccn=12, exit_guards=0, dispatch_share=1.0, dispatch_arm=0)
    assert judged_leaves_out(guards) == "every repeated error guard"
    assert judged_leaves_out(dispatch) == "every arm of its dispatch"


_C_LABELS = """
int retry(int fd) {
    if (fd < 0) goto out;
again:
    if (busy(fd)) goto again;
    return 0;
out:
    return -1;
}
"""


def test_c_a_label_ends_the_run() -> None:
    assert _fn("c", _C_LABELS, "retry").exit_guards == 0


_SH_SUCCESS = """
check() {
  [ -n "$done" ] && exit 0
  [ -f "$lock" ] && return
  cached || exit 0
  ready && return
}
"""


def test_shell_success_exits_are_not_error_guards() -> None:
    assert _fn("shell", _SH_SUCCESS, "check").exit_guards == 0


_GO_STDERR = """
package p

func pipes(c *Cmd) {
	if stderr != nil {
		return
	}
	if stderr != nil {
		return
	}
}
"""


def test_go_stderr_is_not_an_error_value() -> None:
    assert _fn("go", _GO_STDERR, "pipes").exit_guards == 0
