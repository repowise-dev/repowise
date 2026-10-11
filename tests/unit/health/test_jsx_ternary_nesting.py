"""Markup and ternary dispatch are structure, not control-flow nesting.

A view that picks one of ten panels with a ternary chain, or renders markup
conditionally inside markup, read as "nests 10 levels deep". Those ternaries
still count toward CCN; they open no nesting level. Real ``if`` / ``for`` /
``try`` nesting, including inside a handler written in JSX, still does.
"""

from __future__ import annotations

import pytest

from repowise.core.analysis.health.biomarkers.base import FileContext
from repowise.core.analysis.health.biomarkers.nested_complexity import BIOMARKER
from repowise.core.analysis.health.complexity import walk_file


def _require(language: str) -> None:
    from repowise.core.ingestion.parser import _get_language

    if _get_language(language) is None:
        pytest.skip(f"tree-sitter language pack missing for {language}")


def _fn(src: str, name: str, language: str = "tsx", path: str = "/tmp/view.tsx"):
    _require(language)
    fns = {f.name: f for f in walk_file(path, language, src.encode()).functions}
    assert name in fns, sorted(fns)
    return fns[name]


def _ctx(fn) -> FileContext:
    return FileContext(
        file_path="view.tsx", language="tsx", nloc=40, has_test_file=False, module=None,
        all_functions=(fn,),
    )


# The shape of the dogfood example: one ternary chain choosing a panel, ten arms.
SETTINGS_VIEW = """
export function SettingsView({ view, onClose }: Props) {
  return (
    <Overlay onClose={onClose}>
      <Main>
        {view === 'appearance' ? (
          <Appearance />
        ) : view === 'about' ? (
          <About />
        ) : view === 'gateway' ? (
          <Gateway />
        ) : view === 'keybinds' ? (
          <Keybinds />
        ) : view.startsWith('config:') ? (
          <Config section={view.slice(7)} />
        ) : view === 'providers' ? (
          <Providers />
        ) : view === 'keys' ? (
          <Keys />
        ) : view === 'notifications' ? (
          <Notifications />
        ) : view === 'billing' ? (
          <Billing />
        ) : view === 'plugins' ? (
          <Plugins />
        ) : (
          <Sessions />
        )}
      </Main>
    </Overlay>
  )
}
"""

# Conditional rendering inside conditional rendering: markup, not logic.
NESTED_RENDER = """
export function Inspector({ entry, open, loading, error, rows }: Props) {
  return entry ? (
    <div>
      {open ? (
        loading ? (
          <Spinner />
        ) : error ? (
          <Alert>{error.length > 0 ? <p>{error}</p> : null}</Alert>
        ) : (
          <List>{rows.map(r => (r.ok ? <Ok row={r} /> : <Bad row={r} />))}</List>
        )
      ) : null}
    </div>
  ) : null
}
"""

# A real pyramid inside a handler written in JSX.
HANDLER_PYRAMID = """
export function Panel({ items, save }: Props) {
  return (
    <Button
      onClick={() => {
        for (const item of items) {
          if (item.dirty) {
            try {
              if (item.remote) {
                while (item.retries > 0) {
                  save(item)
                }
              }
            } catch (err) {
              console.error(err)
            }
          }
        }
      }}
    />
  )
}
"""


def test_settings_view_ternary_dispatch_is_flat():
    fn = _fn(SETTINGS_VIEW, "SettingsView")
    assert fn.ccn == 11  # every arm is still a decision
    assert fn.max_nesting == 0
    assert fn.cognitive == 10  # a flat +1 per arm, like an ``else if``
    assert BIOMARKER.detect(_ctx(fn)) == []


def test_conditional_rendering_inside_markup_is_flat():
    fn = _fn(NESTED_RENDER, "Inspector")
    assert fn.ccn == 7
    assert fn.max_nesting == 0
    assert fn.cognitive == 6


def test_real_pyramid_inside_a_jsx_handler_still_flagged():
    fn = _fn(HANDLER_PYRAMID, "Panel")
    # for > if > try > if > while: the arrow handler rolls into Panel.
    assert fn.max_nesting == 5
    [finding] = BIOMARKER.detect(_ctx(fn))
    assert finding.severity.value == "high"


def test_value_ternary_chain_is_flat_in_plain_typescript():
    src = """
function label(status: string, muted: boolean): string {
  return status === 'speaking'
    ? 'Speaking'
    : status === 'transcribing'
      ? 'Transcribing'
      : (status === 'thinking' ? 'Thinking' : muted ? 'Muted' : 'Listening')
}
"""
    fn = _fn(src, "label", "typescript", "/tmp/label.ts")
    assert fn.ccn == 5
    assert fn.max_nesting == 1  # the head opens one level, the arms none
    assert fn.cognitive == 4  # head 1 + a flat 1 per arm


def test_ternary_in_a_consequence_still_nests():
    src = """
function pick(err: unknown, active: boolean): string {
  return err instanceof Error ? (active ? 'accept failed' : 'dismiss failed') : 'failed'
}
"""
    assert _fn(src, "pick", "typescript", "/tmp/pick.ts").max_nesting == 2


def test_pyramid_inside_a_map_arrow_in_jsx_still_nests():
    src = """
export function List({ items }: Props) {
  return (
    <ul>
      {items.map(i => {
        if (i.a) {
          for (const x of i.xs) {
            if (x.b) {
              if (x.c) {
                track(x)
              }
            }
          }
        }
        return <li key={i.id} />
      })}
    </ul>
  )
}
"""
    assert _fn(src, "List").max_nesting == 4


def test_value_ternary_in_a_call_argument_inside_jsx_still_nests():
    src = """
export function Label({ a, b }: Props) {
  return <span>{fmt(a ? (b ? 1 : 2) : 3)}</span>
}
"""
    assert _fn(src, "Label").max_nesting == 2


def test_value_ternary_in_an_object_literal_inside_jsx_still_nests():
    src = """
export function Dot({ a, b }: Props) {
  return <div style={{ color: a ? (b ? 'red' : 'green') : 'blue' }} />
}
"""
    assert _fn(src, "Dot").max_nesting == 2


def test_ternary_whose_branch_is_not_jsx_nests_outside_markup():
    src = """
export function Maybe({ a, b }: Props) {
  const el = a ? b && <A /> : null
  return el
}
"""
    assert _fn(src, "Maybe").max_nesting == 1


def test_python_conditional_expression_chain_is_flat():
    # ``#3278`` left Python nesting a conditional-expression chain one level
    # per arm; the chain now reads like an ``elif`` dispatch. The else-arm of
    # ``X if C else Y`` is the node's last named child (the grammar names no
    # ``alternative`` field), so the arm is found positionally.
    src = "def f(a, b):\n    return 1 if a else 2 if b else 3\n"
    fn = _fn(src, "f", "python", "/tmp/f.py")
    assert (fn.ccn, fn.max_nesting) == (3, 1)
    assert fn.cognitive == 2  # head 1 + a flat 1 per arm, like an ``elif``


def test_python_longer_chain_is_flat():
    src = (
        "def f(a, b, c):\n"
        "    return 1 if a else 2 if b else 3 if c else 4\n"
    )
    fn = _fn(src, "f", "python", "/tmp/f.py")
    assert fn.ccn == 4
    assert fn.max_nesting == 1  # the head opens one level, the arms none
    assert fn.cognitive == 3


def test_python_parenthesized_chain_arm_is_flat():
    src = "def f(a, b, c):\n    return 1 if a else (2 if b else 3)\n"
    fn = _fn(src, "f", "python", "/tmp/f.py")
    assert fn.max_nesting == 1


def test_python_conditional_in_a_consequence_still_nests():
    # A conditional in another's consequence is a decision inside a decision.
    src = "def g(a, b):\n    return (1 if a else 2) if b else 3\n"
    fn = _fn(src, "g", "python", "/tmp/g.py")
    assert fn.max_nesting == 2


def test_python_conditional_inside_an_if_still_nests():
    # The ``if`` opens a level; the conditional inside it opens a second.
    src = "def h(a, b):\n    if a:\n        return 1 if b else 2\n    return 0\n"
    fn = _fn(src, "h", "python", "/tmp/h.py")
    assert fn.max_nesting == 2
