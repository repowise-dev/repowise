/**
 * Colours for the canvas: CSS strings (token `var()`s and `color-mix()`es)
 * resolved to RGB once per theme through the browser's own computed style, so
 * the key and the field read one source.
 */

export type Rgb = readonly [number, number, number];

const FALLBACK: Rgb = [128, 128, 128];

/** Parse a computed colour: `rgb()`, `rgba()`, `color(srgb …)` or hex. */
export function parseCssColour(value: string): Rgb | null {
  const v = value.trim();
  let m = /^rgba?\(\s*([\d.]+)[\s,]+([\d.]+)[\s,]+([\d.]+)/i.exec(v);
  if (m) return [Number(m[1]), Number(m[2]), Number(m[3])];
  m = /^color\(srgb\s+([\d.e-]+)\s+([\d.e-]+)\s+([\d.e-]+)/i.exec(v);
  if (m) return [Number(m[1]) * 255, Number(m[2]) * 255, Number(m[3]) * 255];
  m = /^#([0-9a-f]{6})$/i.exec(v);
  if (m) {
    const n = parseInt(m[1]!, 16);
    return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
  }
  return null;
}

/**
 * A cached CSS-colour-to-RGB resolver bound to one element, so `var()` reads
 * the theme in force there. Build a new one when the theme flips.
 */
export function createColourResolver(host: HTMLElement | null): (css: string) => Rgb {
  const cache = new Map<string, Rgb>();
  let probe: HTMLSpanElement | null = null;
  return (css) => {
    const hit = cache.get(css);
    if (hit) return hit;
    let rgb: Rgb | null = parseCssColour(css);
    if (!rgb && host && typeof getComputedStyle !== "undefined") {
      if (!probe) {
        probe = document.createElement("span");
        probe.style.display = "none";
        host.appendChild(probe);
      }
      probe.style.color = "";
      probe.style.color = css;
      rgb = parseCssColour(getComputedStyle(probe).color);
    }
    const out = rgb ?? FALLBACK;
    cache.set(css, out);
    return out;
  };
}

export function rgba([r, g, b]: Rgb, a = 1): string {
  return `rgba(${Math.round(r)},${Math.round(g)},${Math.round(b)},${a})`;
}

export function mixRgb(a: Rgb, b: Rgb, t: number): Rgb {
  return [a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t, a[2] + (b[2] - a[2]) * t];
}

/** Theme colours the field paints with besides the node fills. */
export interface Palette {
  ink: Rgb;
  ring: Rgb;
  accent: Rgb;
}

export function resolvePalette(resolve: (css: string) => Rgb): Palette {
  return {
    ink: resolve("var(--color-text-primary)"),
    ring: resolve("var(--color-border-default)"),
    accent: resolve("var(--color-accent-primary)"),
  };
}

