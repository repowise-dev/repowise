# October Sunset — Design Tokens & Contrast Matrix (Phase 0)

> Source of truth for the redesign. These are the **resolved** semantic token
> values — adjusted from the plan's design-intent table to pass the WCAG
> contrast floors. Run `python3 docs/design/contrast_check.py` to regenerate
> the matrix; it exits non-zero if any required pair regresses (CI gate).

## Floors (both light + dark)

- Body text on primary surfaces — **≥ 7.0** (AAA)
- Secondary / large / status text, accent-on-surface — **≥ 4.5** (AA)
- Interactive / non-text UI (active borders, hints) — **≥ 3.0**

## §6 decisions (resolved)

1. **Default theme — Dark.** Preserves current product behavior; System/Light
   are opt-in via the toggle. (`next-themes` `defaultTheme="dark"`,
   `enableSystem`.)
2. **Light-mode accent strategy.** `--color-accent-primary` is darkened to
   `#B95319` so accent *text/icons/links* clear AA on white **and** warm paper.
   The vivid sunset (`#F27F3D`) is reserved for *fills* via the new
   `--color-accent-fill`. Plum (`--color-accent-secondary`) is used for
   links-alt **and** data-viz.
3. **Gradient intensity.** Restrained in-product (active states, small accent
   washes); bold in marketing (full hero meshes).

### The fill-vs-text split (why two accent tokens)

A single accent can't be both AA-as-text on white *and* vivid-as-a-CTA. So:

| Token | Light | Dark | Used for |
|-------|-------|------|----------|
| `--color-accent-primary` | `#B95319` | `#F2A03D` | accent **text, icons, links, borders**; also a fill (white text on it) |
| `--color-accent-fill` | `#F27F3D` | `#F27F3D` | vivid **CTA / selected fills** (dark text on it) |
| `--color-text-inverse` | `#FFFFFF` | `#17121C` | text on an `accent-primary` fill |
| `--color-text-on-accent` | `#241B2C` | `#17121C` | text on the vivid `accent-fill` |

White on the vivid `#F27F3D` is only 2.67:1 — so CTAs use **dark** text
(`text-on-accent`, 6.21:1). Existing `bg-accent-primary text-text-inverse`
fills keep working because `text-inverse` flips per theme (white in light on
the deep `#B95319` = 4.87:1; near-black in dark on bright `#F2A03D` = 8.66:1).

## Resolved core semantic tokens

| Token | Light (`:root`) | Dark (`.dark`) |
|-------|-----------------|----------------|
| `--color-bg-root` | `#FBF6F1` | `#17121C` |
| `--color-bg-surface` | `#FFFFFF` | `#1F1826` |
| `--color-bg-elevated` | `#FBF4EE` | `#271F30` |
| `--color-bg-overlay` | `#FFFFFF` | `#2D2438` |
| `--color-bg-inset` | `#F4EAE1` | `#120E17` |
| `--color-border-default` | `rgba(88,67,108,.12)` | `rgba(255,240,230,.08)` |
| `--color-border-hover` | `rgba(88,67,108,.22)` | `rgba(255,240,230,.16)` |
| `--color-border-active` | `rgba(192,78,20,.80)` | `rgba(242,160,61,.55)` |
| `--color-text-primary` | `#241B2C` | `#F5EAE3` |
| `--color-text-secondary` | `#5E5360` | `#B7A9B2` |
| `--color-text-tertiary` | `#8C7F88` | `#7E7186` |
| `--color-text-inverse` | `#FFFFFF` | `#17121C` |
| `--color-text-on-accent` | `#241B2C` | `#17121C` |
| `--color-accent-primary` | `#B95319` | `#F2A03D` |
| `--color-accent-fill` | `#F27F3D` | `#F27F3D` |
| `--color-accent-hover` | `#9A4513` | `#F7B65E` |
| `--color-accent-muted` | `rgba(242,127,61,.12)` | `rgba(242,160,61,.16)` |
| `--color-accent-secondary` | `#58436C` | `#A98FC4` |

### Status (functional, tuned)

| Token | Light | Dark |
|-------|-------|------|
| `--color-success` / `--color-confidence-fresh` | `#1D8155` | `#34D399` |
| `--color-warning` / `--color-confidence-stale` | `#9A6614` | `#F2A03D` |
| `--color-error` / `--color-confidence-outdated` | `#B23A2E` | `#E06A5A` |
| `--color-info` | `#58436C` | `#A98FC4` |

## Brand primitive ramps (theme-independent)

```
--sunset-50:#FFF4ED  --sunset-100:#FFE6D5 --sunset-200:#FFC4B1 --sunset-300:#F7A56E
--sunset-400:#F2A03D --sunset-500:#F27F3D --sunset-600:#E0651F --sunset-700:#C04E14
--sunset-800:#8C3D35 --sunset-900:#5E2A22
--plum-50:#F4F1F8 --plum-100:#E7E1F0 --plum-200:#CFC3DF --plum-300:#A98FC4
--plum-400:#826AA0 --plum-500:#58436C --plum-600:#473659 --plum-700:#362945
--plum-800:#261C32 --plum-900:#1A1320
--clay:#8C3D35  --peach:#FFC4B1  --cream:#FCE9DD  --amber:#F2A03D
```

## Signature gradients

```
--gradient-sunset: linear-gradient(135deg,#58436C 0%,#F27F3D 55%,#F2A03D 100%)
--gradient-ember:  linear-gradient(135deg,#F27F3D 0%,#F2A03D 100%)
--gradient-peach:  linear-gradient(160deg,#FFC4B1 0%,#F2A03D 100%)
--gradient-plum:   linear-gradient(135deg,#362945 0%,#58436C 100%)
```

Gradients are for hero washes, primary CTAs, brand marks, empty-state art, and
selected/active accents — never behind body text. Text over gradients uses
`--color-text-on-accent` / `--color-text-inverse` with a verified ≥4.5:1 floor.

## Contrast matrix (generated)

<!-- Regenerate with: python3 docs/design/contrast_check.py -->

### Light mode
| Pair | Ratio | Floor | Result |
|------|-------|-------|--------|
| Body text on page | 15.42 | 7.0 | PASS |
| Body text on card | 16.56 | 7.0 | PASS |
| Body text on elevated | 15.20 | 7.0 | PASS |
| Secondary text on page | 6.78 | 4.5 | PASS |
| Secondary text on card | 7.29 | 4.5 | PASS |
| Tertiary/hint on card | 3.81 | 3.0 | PASS |
| Accent text on card | 4.87 | 4.5 | PASS |
| Accent text on page | 4.53 | 4.5 | PASS |
| Plum link-alt on card | 8.63 | 4.5 | PASS |
| Text on accent fill (CTA) | 6.21 | 4.5 | PASS |
| Text on accent-primary fill | 4.87 | 4.5 | PASS |
| Success text on card | 4.86 | 4.5 | PASS |
| Warning text on card | 4.90 | 4.5 | PASS |
| Error text on card | 5.94 | 4.5 | PASS |
| Info text on card | 8.63 | 4.5 | PASS |
| Active border on card | 3.47 | 3.0 | PASS |

### Dark mode
| Pair | Ratio | Floor | Result |
|------|-------|-------|--------|
| Body text on page | 15.58 | 7.0 | PASS |
| Body text on card | 14.60 | 7.0 | PASS |
| Body text on elevated | 13.40 | 7.0 | PASS |
| Secondary text on page | 8.19 | 4.5 | PASS |
| Secondary text on card | 7.67 | 4.5 | PASS |
| Tertiary/hint on card | 3.77 | 3.0 | PASS |
| Accent text on card | 8.11 | 4.5 | PASS |
| Accent text on page | 8.66 | 4.5 | PASS |
| Plum link-alt on card | 6.09 | 4.5 | PASS |
| Text on accent fill (CTA) | 6.91 | 4.5 | PASS |
| Text on accent-primary fill | 8.66 | 4.5 | PASS |
| Success text on card | 8.98 | 4.5 | PASS |
| Warning text on card | 8.11 | 4.5 | PASS |
| Error text on card | 5.24 | 4.5 | PASS |
| Info text on card | 6.09 | 4.5 | PASS |
| Active border on card | 3.34 | 3.0 | PASS |

**32 pairs · 0 failures.**
