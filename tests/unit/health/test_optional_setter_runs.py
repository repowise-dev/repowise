"""A run of optional setters counts once in the CCN the complexity markers judge.

Each ``if`` sets one field when its option is there: Rust ``if let Some(v) =
opt { b.v(v); }``, Go ``if config.DryRun { tx.DryRun = true }``, Python
``if "ciphers" in opts: ctx.set_ciphers(opts["ciphers"])``, Java
``if (a.getX() == null && b.getX() != null) a.setX(b.getX());``. The branches
do not interact, so the run is one mapping step. The stored CCN is unchanged.
"""

from __future__ import annotations

import pytest

from repowise.core.analysis.health.complexity import walk_file
from repowise.core.analysis.health.complexity.dispatch import judged_ccn

_EXT = {"rust": "rs", "go": "go", "python": "py", "java": "java", "fsharp": "fs"}


def _fn(language: str, source: str, name: str):
    fcx = walk_file(f"/tmp/sample.{_EXT[language]}", language, source.encode())
    if not fcx.functions:
        pytest.skip(f"{language} grammar unavailable")
    return {fc.name: fc for fc in fcx.functions}[name]


_RUST = """
fn fetch(req: Request, init: Init) {
    if !req.cors {
        init.mode(NoCors);
    }
    if let Some(creds) = req.credentials {
        init.credentials(creds);
    }
    if let Some(cache) = req.cache {
        init.set_cache(cache);
    }
    if let Some(v) = req.version {
        builder = builder.version(v)
    }
}

fn decides(req: Request, init: Init) {
    if let Some(body) = req.body {
        init.body(other);
    }
    if let Some(t) = req.timeout {
        init.reset();
    }
}
"""


def test_rust_if_let_setters_count_once():
    fn = _fn("rust", _RUST, "fetch")
    assert fn.ccn == 5
    assert fn.copy_guards == 2
    assert judged_ccn(fn) == 3


def test_rust_if_let_whose_body_ignores_the_binding_is_a_decision():
    fn = _fn("rust", _RUST, "decides")
    assert fn.copy_guards == 0


_GO = """
package gorm

func (db *DB) Session(config *Session) *DB {
	if config.CreateBatchSize > 0 {
		tx.Config.CreateBatchSize = config.CreateBatchSize
	}
	if config.SkipDefaultTransaction {
		tx.Config.SkipDefaultTransaction = true
	}
	if config.AllowGlobalUpdate {
		txConfig.AllowGlobalUpdate = true
	}
	if config.Context != nil {
		tx.Statement.Context = config.Context
	}
	return tx
}

func decides(config *Session, start, n int) {
	if start < 0 {
		start = n + start
	}
	if start > n {
		start = n
	}
	if config.Verbose {
		tx.Logger = verbose
	}
	if !ok(config.Name) {
		t.Errorf("bad name %v", config.Name)
	}
}
"""


def test_go_flag_and_value_setters_count_once():
    fn = _fn("go", _GO, "Session")
    assert fn.ccn == 5
    # The ``> 0`` bound is a decision; the three after it are one run.
    assert fn.copy_guards == 2
    assert judged_ccn(fn) == 3


def test_go_bounds_unrelated_fields_and_negated_checks_are_decisions():
    fn = _fn("go", _GO, "decides")
    assert fn.copy_guards == 0


_PY = """
def ssl_options_to_context(ssl_options, keyfile=None, certfile=None):
    if keyfile is None:
        keyfile = {}
    if certfile is None:
        certfile = {}
    if "ca_certs" in ssl_options:
        context.load_verify_locations(ssl_options["ca_certs"])
    if "ciphers" in ssl_options:
        context.set_ciphers(ssl_options["ciphers"])
    return context


def decides(options, other=None):
    if other is None:
        fallback = {}
    if "ciphers" not in options:
        options["ciphers"] = DEFAULT
    if not options:
        reset()
"""


def test_python_none_fills_and_membership_setters_count_once():
    fn = _fn("python", _PY, "ssl_options_to_context")
    assert fn.ccn == 5
    assert fn.copy_guards == 3
    assert judged_ccn(fn) == 2


def test_python_fill_of_another_name_is_a_decision():
    fn = _fn("python", _PY, "decides")
    assert fn.copy_guards == 0


_JAVA = """
class ConfigUtils {
  static void merge(Props instance, Props base) {
    if (instance.getRegisterHealthIndicator() == null &&
        base.getRegisterHealthIndicator() != null) {
      instance.setRegisterHealthIndicator(base.getRegisterHealthIndicator());
    }
    if (instance.getEventConsumerBufferSize() == null &&
        base.getEventConsumerBufferSize() != null) {
      instance.setEventConsumerBufferSize(base.getEventConsumerBufferSize());
    }
    if (instance.getTimeout() == null && base.getTimeout() != null) instance.setTimeout(base.getTimeout());
  }

  static void decides(Props instance, Props base) {
    if (instance.getA() == null || base.getA() != null) {
      instance.setA(base.getA());
    }
    if (instance.getB() == null && base.isLocked()) {
      instance.setB(base.getB());
    }
  }
}
"""


def test_java_null_fill_conjunctions_count_once():
    fn = _fn("java", _JAVA, "merge")
    assert fn.ccn == 7
    # Each later fill's two conditions repeat the first's.
    assert fn.copy_guards == 4
    assert judged_ccn(fn) == 3


def test_java_either_or_and_unrelated_tests_are_decisions():
    fn = _fn("java", _JAVA, "decides")
    assert fn.copy_guards == 0


_FS = """
module Process

let asStartInfo (x: ProcStartInfo) =
    let p = ProcessStartInfo(x.FileName, x.Arguments)
    if not (isNull x.Domain) then
        p.Domain <- x.Domain
    if x.LoadUserProfile then
        p.LoadUserProfile <- x.LoadUserProfile
    if not (isNull x.UserName) then
        p.UserName <- x.UserName
    p
"""


def test_fsharp_optional_property_copies_count_once():
    fn = _fn("fsharp", _FS, "asStartInfo")
    assert fn.ccn == 4
    assert fn.copy_guards == 2
    assert judged_ccn(fn) == 2
