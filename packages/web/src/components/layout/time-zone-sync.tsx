"use client";

import * as React from "react";
import { useRouter } from "next/navigation";
import { browserTimeZone } from "@repowise-dev/ui/lib/format";
import {
  AUTO_TIME_ZONE,
  DETECTED_TIME_ZONE_COOKIE,
  TIME_ZONE_COOKIE,
  readCookie,
  resolveViewerTimeZone,
  timeZoneCookie,
} from "@/i18n/time-zone";

/**
 * Tells the server this browser's zone when the viewer is on "auto".
 *
 * The server cannot know the zone before the browser reports it, so a first
 * visit renders in UTC and hydrates in UTC (no mismatch). After mount this
 * writes the detected zone to a cookie and refreshes the server tree once, so
 * every later render, server and client, is already in the right zone. The
 * same path catches a laptop that changed zones since the last visit.
 *
 * Renders nothing. Runs once per page load, so a browser that refuses cookies
 * cannot loop.
 */
export function TimeZoneSync({ zone }: { zone: string }) {
  const router = useRouter();
  const done = React.useRef(false);
  React.useEffect(() => {
    if (done.current) return;
    done.current = true;
    // An unknown or invalid preference counts as auto, as it does on the server.
    const { preference } = resolveViewerTimeZone(readCookie(TIME_ZONE_COOKIE), undefined);
    if (preference !== AUTO_TIME_ZONE) return;
    const detected = browserTimeZone();
    // Already reported and still not rendered: the server's Intl does not
    // know this zone. Refreshing again would only repeat on every load.
    if (detected === zone || readCookie(DETECTED_TIME_ZONE_COOKIE) === detected) return;
    document.cookie = timeZoneCookie(DETECTED_TIME_ZONE_COOKIE, detected);
    router.refresh();
    // `zone` is the server's answer at mount; a later change comes from this
    // refresh and must not trigger another one.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  return null;
}
