"""A CLI failure that says why, not just that.

``ClickException`` carries a message and nothing else, so the outcome recorded
for a failed command was the class name alone. 1,612 installs a month die on a
bare ``ClickException`` in ``init`` and the reason is not recoverable after the
fact: a bad path, a cost gate with no terminal to confirm on and a hand-edited
editor config that will not parse are indistinguishable.

Two decisions here, both about not disturbing what is already being measured.

The reason travels *on the exception* rather than being stashed beside it. A
raise site that records into the invocation's outcome and then raises reports a
failure the command may never have: ``init`` catches the no-provider exception
and renders a template wiki instead, so the run succeeds while the outcome still
claims it failed for want of a provider. Attaching it to the exception means only
the one that actually terminates the command is ever recorded, which is what the
root group does with it.

And it is an attribute on a plain ``ClickException``, not a subclass. The outcome
records ``type(exc).__name__``, so a subclass would silently split the very
histogram this exists to make readable - converted sites in one bucket,
unconverted ones in another, with the before-and-after no longer comparable.
"""

from __future__ import annotations

import click


def reasoned_error(message: str, *, reason: str) -> click.ClickException:
    """A ``ClickException`` that also names a coarse, non-identifying reason.

    ``reason`` is a stable snake_case slug, never a message and never anything
    derived from user input: it lands in anonymous telemetry under the same
    privacy contract as every other outcome field.
    """
    exc = click.ClickException(message)
    exc.reason = reason
    return exc
