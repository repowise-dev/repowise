import { cookies } from "next/headers";
import { getRequestConfig } from "next-intl/server";
import { setDisplayTimeZone } from "@repowise-dev/ui/lib/format";
import { DEFAULT_LOCALE, LOCALE_COOKIE, resolveLocale, type Locale } from "./config";
import { withFallback } from "./fallback";
import {
  DETECTED_TIME_ZONE_COOKIE,
  TIME_ZONE_COOKIE,
  resolveViewerTimeZone,
} from "./time-zone";
import en from "../../messages/en.json";
import zhCN from "../../messages/zh-CN.json";

/**
 * next-intl request config — the no-prefix (cookie) strategy.
 *
 * There is no i18n routing: the locale is never part of the URL. The server
 * reads `NEXT_LOCALE` from the request cookies on every render, so a request
 * carrying `Cookie: NEXT_LOCALE=<tag>` renders that language server-side and
 * an unset cookie renders English.
 *
 * English is the source of truth: a locale catalog is layered on top of
 * `en.json` (`withFallback`), so a key its translator has not reached yet
 * renders the English string instead of the raw key or a `MISSING_MESSAGE`
 * throw. That is what lets UI copy change without every locale being updated
 * in the same commit.
 *
 * Messages are imported statically rather than read from disk at runtime: the
 * build is `output: "standalone"`, and a `fs.readFile` on a computed path is
 * not traced into the standalone bundle, so the JSON would be missing in a
 * production container.
 */
const MESSAGES: Record<Locale, Record<string, unknown>> = {
  en,
  "zh-CN": zhCN,
};

export default getRequestConfig(async ({ requestLocale }) => {
  // `requestLocale` is the routing-provided locale; with no routing configured
  // it is undefined, so the cookie is the authority. Kept in the chain so the
  // config keeps working if prefix routing is ever turned on.
  const fromRouting = await requestLocale;
  const jar = await cookies();
  const fromCookie = jar.get(LOCALE_COOKIE)?.value;
  const locale: Locale = resolveLocale(fromRouting ?? fromCookie ?? DEFAULT_LOCALE);
  const { zone } = resolveViewerTimeZone(
    jar.get(TIME_ZONE_COOKIE)?.value,
    jar.get(DETECTED_TIME_ZONE_COOKIE)?.value,
  );
  // Server components format dates through format.ts, which has no request
  // context of its own. This config runs once per request, first thing (the
  // root layout's getLocale), so it is where the server realm learns the
  // zone. The browser and SSR realms get it from <DisplayTimeZone>.
  setDisplayTimeZone(zone);

  return {
    locale,
    messages: withFallback(en, MESSAGES[locale]),
    // The viewer's zone, from the same cookies on every render, so the server
    // render and the browser agree.
    timeZone: zone,
  };
});
