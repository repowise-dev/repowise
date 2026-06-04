#!/usr/bin/env python3
"""October Sunset — WCAG contrast verification.

Computes WCAG 2.1 relative-luminance contrast ratios for every critical
text/surface, accent-on-surface, and status pair in both light and dark
modes, and asserts the floors from the redesign plan:

  - body text on primary surfaces        >= 7.0  (AAA)
  - secondary text / large text / status >= 4.5  (AA)
  - interactive / non-text UI            >= 3.0

Semi-transparent tokens (borders, muted fills) are alpha-composited over
their surface before the ratio is computed, so the numbers reflect what a
user actually sees. Run:  python3 docs/design/contrast_check.py
Exits non-zero if any required pair misses its floor (CI gate).
"""
from __future__ import annotations

import re
import sys


# --------------------------------------------------------------------------
# Color helpers
# --------------------------------------------------------------------------
def _hex_to_rgb(h: str) -> tuple[float, float, float]:
    h = h.lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    return tuple(int(h[i : i + 2], 16) / 255 for i in (0, 2, 4))


def _parse(color: str) -> tuple[float, float, float, float]:
    """Return (r, g, b, alpha) in 0..1 from a hex or rgba() string."""
    color = color.strip()
    if color.startswith("#"):
        r, g, b = _hex_to_rgb(color)
        return r, g, b, 1.0
    m = re.match(r"rgba?\(([^)]+)\)", color)
    if not m:
        raise ValueError(f"unparseable color: {color}")
    parts = [p.strip() for p in m.group(1).split(",")]
    r, g, b = (int(parts[i]) / 255 for i in range(3))
    a = float(parts[3]) if len(parts) > 3 else 1.0
    return r, g, b, a


def _composite(fg: str, bg: str) -> tuple[float, float, float]:
    """Alpha-composite fg over an opaque bg, return opaque rgb."""
    fr, fg_, fb, fa = _parse(fg)
    br, bg_, bb, _ = _parse(bg)
    return (
        fr * fa + br * (1 - fa),
        fg_ * fa + bg_ * (1 - fa),
        fb * fa + bb * (1 - fa),
    )


def _lin(c: float) -> float:
    return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4


def _lum(rgb: tuple[float, float, float]) -> float:
    r, g, b = (_lin(c) for c in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(fg: str, bg: str) -> float:
    """WCAG contrast ratio of fg over (opaque) bg, compositing alpha first."""
    fg_rgb = _composite(fg, bg)
    bg_rgb = _composite(bg, bg)
    l1, l2 = _lum(fg_rgb), _lum(bg_rgb)
    hi, lo = max(l1, l2), min(l1, l2)
    return (hi + 0.05) / (lo + 0.05)


# --------------------------------------------------------------------------
# Token values (must mirror packages/ui/styles/globals.css)
# --------------------------------------------------------------------------
LIGHT = {
    "bg-root": "#FBF6F1",
    "bg-surface": "#FFFFFF",
    "bg-elevated": "#FBF4EE",
    "bg-inset": "#F4EAE1",
    "text-primary": "#241B2C",
    "text-secondary": "#5E5360",
    "text-tertiary": "#8C7F88",
    "text-inverse": "#FFFFFF",
    "text-on-accent": "#241B2C",
    "accent-primary": "#A16215",
    "accent-fill": "#F59520",
    "accent-secondary": "#58436C",
    "border-default": "rgba(88,67,108,0.12)",
    "border-active": "rgba(176,107,18,0.90)",
    "success": "#1D8155",
    "warning": "#9A6614",
    "error": "#B23A2E",
    "info": "#58436C",
}

DARK = {
    "bg-root": "#17131D",
    "bg-surface": "#211B29",
    "bg-elevated": "#2A2335",
    "bg-inset": "#110D17",
    "text-primary": "#EEEAF4",
    "text-secondary": "#A79DB3",
    "text-tertiary": "#786F84",
    "text-inverse": "#17131D",
    "text-on-accent": "#17131D",
    "accent-primary": "#F59520",
    "accent-fill": "#F59520",
    "accent-secondary": "#A98FC4",
    "border-default": "rgba(213,197,232,0.10)",
    "border-active": "rgba(245,149,32,0.55)",
    "success": "#34D399",
    "warning": "#F2A03D",
    "error": "#E06A5A",
    "info": "#A98FC4",
}

# (fg, bg, floor, label)  — floor is the WCAG ratio the pair must meet.
CHECKS = [
    ("text-primary", "bg-root", 7.0, "Body text on page"),
    ("text-primary", "bg-surface", 7.0, "Body text on card"),
    ("text-primary", "bg-elevated", 7.0, "Body text on elevated"),
    ("text-secondary", "bg-root", 4.5, "Secondary text on page"),
    ("text-secondary", "bg-surface", 4.5, "Secondary text on card"),
    ("text-tertiary", "bg-surface", 3.0, "Tertiary/hint on card"),
    ("accent-primary", "bg-surface", 4.5, "Accent text on card"),
    ("accent-primary", "bg-root", 4.5, "Accent text on page"),
    ("accent-secondary", "bg-surface", 4.5, "Plum link-alt on card"),
    ("text-on-accent", "accent-fill", 4.5, "Text on accent fill (CTA)"),
    ("text-inverse", "accent-primary", 4.5, "Text on accent-primary fill"),
    ("success", "bg-surface", 4.5, "Success text on card"),
    ("warning", "bg-surface", 4.5, "Warning text on card"),
    ("error", "bg-surface", 4.5, "Error text on card"),
    ("info", "bg-surface", 4.5, "Info text on card"),
    ("border-active", "bg-surface", 3.0, "Active border on card"),
]


def run() -> int:
    failures = 0
    rows: list[str] = []
    for mode, tokens in (("Light", LIGHT), ("Dark", DARK)):
        rows.append(f"\n### {mode} mode\n")
        rows.append("| Pair | Ratio | Floor | Result |")
        rows.append("|------|-------|-------|--------|")
        for fg, bg, floor, label in CHECKS:
            ratio = contrast(tokens[fg], tokens[bg])
            ok = ratio >= floor
            if not ok:
                failures += 1
            mark = "PASS" if ok else "**FAIL**"
            rows.append(f"| {label} | {ratio:.2f} | {floor:.1f} | {mark} |")
    print("\n".join(rows))
    print(f"\n**Total pairs:** {len(CHECKS) * 2}  ·  **Failures:** {failures}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(run())
