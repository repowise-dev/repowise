import { cookies } from "next/headers";
import { getRequestConfig } from "next-intl/server";
import { DEFAULT_LOCALE, LOCALE_COOKIE, resolveLocale, type Locale } from "./config";
import en from "../../messages/en.json";
import zhCN from "../../messages/zh-CN.json";

/**
 * next-intl request config — the no-prefix (cookie) strategy.
 *
 * There is no i18n routing: the locale is never part of the URL. The server
 * reads `NEXT_LOCALE` from the request cookies on every render, so a request
 * carrying `Cookie: NEXT_LOCALE=zh-CN` renders Chinese server-side (which is
 * what the acceptance check curls for) and an unset cookie renders English.
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
  const fromCookie = (await cookies()).get(LOCALE_COOKIE)?.value;
  const locale = resolveLocale(fromRouting ?? fromCookie ?? DEFAULT_LOCALE);

  return {
    locale,
    messages: MESSAGES[locale],
    // A fixed zone keeps server and client formatting in agreement; the UI
    // renders relative times from the API rather than formatting zones itself.
    timeZone: "UTC",
  };
});
