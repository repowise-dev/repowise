"""Remix, and React Router v7's framework mode."""

from __future__ import annotations

from .base import FrameworkFacts, _js

REMIX = FrameworkFacts(
    name="Remix",
    anchor="framework:remix",
    # React Router v7's framework mode is Remix renamed.
    npm_packages=("@remix-run/dev", "@remix-run/react", "@react-router/dev"),
    entry_globs=_js("app/root", "app/routes", "app/routes/*"),
)

#: React Router v7 framework mode, which loads ``root`` and ``routes`` from the
#: app directory and every route module ``routes`` names.
REACT_ROUTER = FrameworkFacts(name="React Router", anchor="framework:react-router")
