"use client";

import * as React from "react";
import { formatDate, formatRelativeTime, parseDate } from "../lib/format";

const subscribe = () => () => {};

export interface RelativeTimeProps {
  /** The moment the text describes. Null or unparseable renders `children` as is. */
  date: string | Date | null | undefined;
  /** Clock-dependent text shown once hydrated. Defaults to `formatRelativeTime(date)`. */
  children?: React.ReactNode;
  /** Clock-free text shown on the server and while hydrating. Defaults to `formatDate(date)`. */
  stable?: React.ReactNode;
}

/**
 * Relative time that survives a cached server render. "3d ago" depends on the
 * clock that renders it, so a page cached at one moment and hydrated at
 * another mismatches (React error 418) and throws away the server HTML. This
 * renders the UTC date on the server and during hydration, then the relative
 * text; a client-side navigation shows the relative text straight away.
 *
 * Pass `children` for host-worded text (a localised "2d ago", a fallback for a
 * future date); a server component can pass it too, it is only a node.
 */
export function RelativeTime({ date, children, stable }: RelativeTimeProps) {
  const hydrated = React.useSyncExternalStore(
    subscribe,
    () => true,
    () => false,
  );
  const d = date == null ? null : parseDate(date);
  const valid = d != null && !Number.isNaN(d.getTime());
  if (!hydrated && valid) return <>{stable ?? formatDate(d)}</>;
  return <>{children ?? (valid ? formatRelativeTime(d) : null)}</>;
}
