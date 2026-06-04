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
2. **Light-mode accent strategy.** The brand accent stays repowise orange
   `#f59520` — it's `--color-accent-fill` (CTAs, active fills, focus ring) in
   **both** modes. Only accent *text/icons/links* darken to `#A16215`
   (`--color-accent-primary`) in light so they clear AA on white **and** warm
   paper (bright orange can't be AA as small text). Plum
   (`--color-accent-secondary`) is used for links-alt **and** data-viz.
3. **Gradient intensity.** Restrained in-product (active states, small accent
   washes); bold in marketing (full hero meshes).
4. **Dark-mode base — plum-tinted charcoal.** Dark surfaces carry a restrained
   violet cast (`#17131d`→`#322a3e`, text `#eeeaf4`/`#a79db3`, lavender-alpha
   borders) so the product is visually distinct from the crowd of
   neutral-charcoal + orange UIs (user feedback: pure charcoal read "exactly
   like Claude"). The tint is October Sunset's own plum family at low
   saturation; the orange accent still pops. **Light mode is unchanged**
   (warm paper). *(Supersedes the earlier "neutral charcoal, Dark Orange
   UI-kit" decision.)*

### The fill-vs-text split (why two accent tokens)

A single accent can't be both AA-as-text on white *and* vivid-as-a-CTA. So:

| Token | Light | Dark | Used for |
|-------|-------|------|----------|
| `--color-accent-primary` | `#A16215` | `#F59520` | accent **text, icons, links, borders**; also a fill (white text on it) |
| `--color-accent-fill` | `#F59520` | `#F59520` | brand-orange **CTA / selected fills** (dark text on it) |
| `--color-text-inverse` | `#FFFFFF` | `#17131D` | text on an `accent-primary` fill |
| `--color-text-on-accent` | `#241B2C` | `#17131D` | text on the brand `accent-fill` |

White on the bright `#f59520` fill is only ~2.0:1 — so CTAs use **dark** text
(`text-on-accent`, 7.26:1). Existing `bg-accent-primary text-text-inverse`
fills keep working because `text-inverse` flips per theme (white in light on
the deep `#A16215` = 4.91:1; near-black plum in dark on bright `#F59520` = 8.02:1).

## Resolved core semantic tokens

| Token | Light (`:root`) | Dark (`.dark`) |
|-------|-----------------|----------------|
| `--color-bg-root` | `#FBF6F1` | `#17131D` |
| `--color-bg-surface` | `#FFFFFF` | `#211B29` |
| `--color-bg-elevated` | `#FBF4EE` | `#2A2335` |
| `--color-bg-overlay` | `#FFFFFF` | `#322A3E` |
| `--color-bg-inset` | `#F4EAE1` | `#110D17` |
| `--color-border-default` | `rgba(88,67,108,.12)` | `rgba(213,197,232,.10)` |
| `--color-border-hover` | `rgba(88,67,108,.22)` | `rgba(213,197,232,.18)` |
| `--color-border-active` | `rgba(192,78,20,.80)` | `rgba(245,149,32,.55)` |
| `--color-text-primary` | `#241B2C` | `#EEEAF4` |
| `--color-text-secondary` | `#5E5360` | `#A79DB3` |
| `--color-text-tertiary` | `#8C7F88` | `#786F84` |
| `--color-text-inverse` | `#FFFFFF` | `#17131D` |
| `--color-text-on-accent` | `#241B2C` | `#17131D` |
| `--color-accent-primary` | `#A16215` | `#F59520` |
| `--color-accent-fill` | `#F59520` | `#F59520` |
| `--color-accent-fill-hover` | `#E0850F` | `#F7A94D` |
| `--color-accent-hover` | `#824F10` | `#F7A94D` |
| `--color-accent-muted` | `rgba(245,149,32,.12)` | `rgba(245,149,32,.16)` |
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
--gradient-sunset: linear-gradient(135deg,#58436C 0%,#F59520 55%,#F7A94D 100%)
--gradient-ember:  linear-gradient(135deg,#F59520 0%,#F7A94D 100%)
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
| Accent text on card | 4.91 | 4.5 | PASS |
| Accent text on page | 4.58 | 4.5 | PASS |
| Plum link-alt on card | 8.63 | 4.5 | PASS |
| Text on accent fill (CTA) | 7.26 | 4.5 | PASS |
| Text on accent-primary fill | 4.91 | 4.5 | PASS |
| Success text on card | 4.86 | 4.5 | PASS |
| Warning text on card | 4.90 | 4.5 | PASS |
| Error text on card | 5.94 | 4.5 | PASS |
| Info text on card | 8.63 | 4.5 | PASS |
| Active border on card | 3.59 | 3.0 | PASS |

### Dark mode
| Pair | Ratio | Floor | Result |
|------|-------|-------|--------|
| Body text on page | 15.44 | 7.0 | PASS |
| Body text on card | 14.13 | 7.0 | PASS |
| Body text on elevated | 12.74 | 7.0 | PASS |
| Secondary text on page | 7.07 | 4.5 | PASS |
| Secondary text on card | 6.48 | 4.5 | PASS |
| Tertiary/hint on card | 3.51 | 3.0 | PASS |
| Accent text on card | 7.34 | 4.5 | PASS |
| Accent text on page | 8.02 | 4.5 | PASS |
| Plum link-alt on card | 5.91 | 4.5 | PASS |
| Text on accent fill (CTA) | 8.02 | 4.5 | PASS |
| Text on accent-primary fill | 8.02 | 4.5 | PASS |
| Success text on card | 8.71 | 4.5 | PASS |
| Warning text on card | 7.88 | 4.5 | PASS |
| Error text on card | 5.09 | 4.5 | PASS |
| Info text on card | 5.91 | 4.5 | PASS |
| Active border on card | 3.13 | 3.0 | PASS |

**32 pairs · 0 failures.**
