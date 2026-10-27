/**
 * English is the source of truth for the message catalogs.
 *
 * A locale catalog only carries the keys its translator has reached. The rest
 * must render the English string at runtime — not the raw key — so a catalog
 * that lags behind `en.json` never breaks the UI. The rule is enforced here by
 * layering every catalog on top of the English one, rather than by requiring
 * every catalog to carry the identical key set.
 */

type Catalog = Record<string, unknown>;

function isCatalog(value: unknown): value is Catalog {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

/**
 * Deep-merge `override` over `base`. Keys the override omits keep their base
 * value; keys it carries (including nested namespaces) win.
 */
export function withFallback(base: Catalog, override?: Catalog): Catalog {
  if (!override) return base;
  const merged: Catalog = { ...base };
  for (const [key, value] of Object.entries(override)) {
    const baseValue = merged[key];
    merged[key] =
      isCatalog(baseValue) && isCatalog(value)
        ? withFallback(baseValue, value)
        : value;
  }
  return merged;
}
