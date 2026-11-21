"use client";

import * as React from "react";
import { setDisplayTimeZone } from "../lib/format";

export interface DisplayTimeZoneProps {
  /** The viewer's resolved IANA zone, decided by the host on the server. */
  zone: string;
  children: React.ReactNode;
}

/**
 * Sets the zone every format.ts absolute date renders in, for the tree below.
 *
 * The host resolves the zone on the server (the OSS web UI reads a cookie) and
 * passes the same value to the server render and to hydration, so both sides
 * format alike. The assignment runs during render, before any child formats a
 * date, on the server and in the browser.
 *
 * Children are keyed on the zone: format.ts is not a React store, so a client
 * component whose props did not change would otherwise keep text formatted in
 * the old zone. A zone change is rare (first visit, a settings change, travel),
 * which makes a remount the simplest way to redraw every date at once.
 */
export function DisplayTimeZone({ zone, children }: DisplayTimeZoneProps) {
  setDisplayTimeZone(zone);
  return <React.Fragment key={zone}>{children}</React.Fragment>;
}
