/**
 * The viewer's time zone preference, carried in cookies next to `NEXT_LOCALE`
 * so the server render and the browser format dates in the same zone.
 *
 * - `TIME_ZONE_COOKIE` holds the preference: "auto" or an IANA zone name.
 *   Unset means auto.
 * - `DETECTED_TIME_ZONE_COOKIE` holds this browser's own zone, written by the
 *   client (`TimeZoneSync`) so the server can render "auto" without guessing.
 *
 * Per viewer, like the locale, so it is not a repo setting.
 */
import { isTimeZone } from "@repowise-dev/ui/lib/format";

export const TIME_ZONE_COOKIE = "NEXT_TIME_ZONE";
export const DETECTED_TIME_ZONE_COOKIE = "NEXT_DETECTED_TIME_ZONE";

/** The preference value that follows the browser. */
export const AUTO_TIME_ZONE = "auto";

/** One year, in seconds, the same lifetime as the locale cookie. */
const COOKIE_MAX_AGE = 60 * 60 * 24 * 365;

export interface ViewerTimeZone {
  /** "auto" or the IANA zone the viewer picked. */
  preference: string;
  /** The zone dates render in. */
  zone: string;
}

/**
 * Resolve the two cookie values into the zone to render. Never throws: the
 * cookies come from the request, so anything Intl does not accept is ignored.
 * An explicit zone wins, then the detected one, then UTC (a first visit,
 * before the browser has reported its zone).
 */
export function resolveViewerTimeZone(
  preference: string | undefined,
  detected: string | undefined,
): ViewerTimeZone {
  if (preference !== AUTO_TIME_ZONE && isTimeZone(preference)) {
    return { preference, zone: preference };
  }
  return { preference: AUTO_TIME_ZONE, zone: isTimeZone(detected) ? detected : "UTC" };
}

/** A `document.cookie` assignment for one of the two cookies. Not
 *  URI-encoded: zone names are cookie-safe ASCII ("America/Port-au-Prince",
 *  "Etc/GMT+5"), and a raw value reads back the same on the server. */
export function timeZoneCookie(name: string, value: string): string {
  return `${name}=${value}; path=/; max-age=${COOKIE_MAX_AGE}; samesite=lax`;
}

/** A cookie's raw value from `document.cookie`, or undefined. Browser only. */
export function readCookie(name: string): string | undefined {
  const prefix = `${name}=`;
  return document.cookie
    .split("; ")
    .find((c) => c.startsWith(prefix))
    ?.slice(prefix.length);
}
