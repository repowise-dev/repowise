/**
 * Locale registry for the Repowise Web UI.
 *
 * The UI is served from one URL space: no `/{locale}/...` prefix routing, so
 * every existing link, bookmark and uptime probe keeps working. The active
 * locale travels in a cookie (`NEXT_LOCALE`) that the server reads per
 * request, plus a `localStorage` mirror the client writes so the choice
 * survives a cookie clear.
 *
 * Adding a language is three steps — see `packages/web/I18N.md`.
 */

export const LOCALES = ["en", "zh-CN"] as const;

export type Locale = (typeof LOCALES)[number];

/** English stays the default: an unset or unknown cookie renders English. */
export const DEFAULT_LOCALE: Locale = "en";

/** The cookie next-intl's docs use for the no-prefix (cookie) strategy. */
export const LOCALE_COOKIE = "NEXT_LOCALE";

/** Client-side mirror, written next to the cookie by the language switcher. */
export const LOCALE_STORAGE_KEY = "repowise:locale";

export function isLocale(value: unknown): value is Locale {
  return (
    typeof value === "string" && (LOCALES as readonly string[]).includes(value)
  );
}

/** Never throws: an unknown or missing value falls back to the default. */
export function resolveLocale(value: unknown): Locale {
  return isLocale(value) ? value : DEFAULT_LOCALE;
}

/**
 * Endonym labels, in the language's own script. These are deliberately NOT
 * translated — a reader looking for their own language must recognise it in
 * the switcher, and "Chinese" is less useful to them than "中文".
 */
export const LOCALE_LABELS: Record<Locale, string> = {
  en: "English",
  "zh-CN": "中文",
};
