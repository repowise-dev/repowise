"use client";

import * as React from "react";
import {
  formatDate,
  formatDateTime,
  formatRelativeTime,
  parseDate,
  type RelativeTimeWords,
} from "../lib/format";

const subscribe = () => () => {};

export interface RelativeTimeProps {
  /** The moment the text describes. */
  date: string | Date | null | undefined;
  /** Wording around the time, with a `{time}` placeholder: "Synced {time}". */
  template?: string | undefined;
  /** Localised wording per bucket, each with a `{value}` placeholder. */
  words?: Partial<RelativeTimeWords> | undefined;
  /** The whole text when the date is missing, unparseable or in the future.
   *  Unset, a missing date renders nothing and a future one reads "just now". */
  fallback?: string | undefined;
}

/**
 * Relative time that survives a cached server render. "3d ago" depends on the
 * clock that renders it, so a page cached at one moment and hydrated at
 * another mismatches (React error 418) and throws away the server HTML. This
 * renders the UTC date on the server and during hydration, then the relative
 * time from the viewer's clock; a client-side navigation shows the relative
 * time straight away.
 *
 * The time is computed here, never passed in: a server component that
 * computed it would hand over text frozen at render time. Hosts word it with
 * plain strings (`template`, `words`), which can cross the client boundary.
 */
export function RelativeTime({ date, template = "{time}", words, fallback }: RelativeTimeProps) {
  const hydrated = React.useSyncExternalStore(
    subscribe,
    () => true,
    () => false,
  );
  const d = date == null ? null : parseDate(date);
  if (d == null || Number.isNaN(d.getTime())) return fallback ?? null;
  if (hydrated && fallback !== undefined && d.getTime() > Date.now()) return fallback;

  const [before = "", after = ""] = template.split("{time}");
  return (
    <>
      {before}
      <time dateTime={d.toISOString()} title={formatDateTime(d)}>
        {hydrated ? formatRelativeTime(d, words) : formatDate(d)}
      </time>
      {after}
    </>
  );
}
