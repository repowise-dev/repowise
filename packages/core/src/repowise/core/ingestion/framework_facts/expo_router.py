"""Expo Router."""

from __future__ import annotations

from .base import FrameworkFacts

#: Expo Router: every file under ``app/`` is a route, layout or API route.
EXPO_ROUTER = FrameworkFacts(
    name="Expo Router",
    anchor="framework:expo-router",
    npm_packages=("expo-router",),
    entry_globs=("app/*", "src/app/*"),
)
