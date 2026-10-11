"use client";

import { useEffect, useState } from "react";
import { DEFAULT_WEEKEND_PRESET, weekendDaysFor } from "@repowise-dev/ui/stats";
import { config } from "@/lib/config";

/**
 * The reader's weekend definition, as weekday indices (0 = Monday).
 *
 * Read after mount rather than during render: the preference lives in
 * localStorage, so the server-rendered pass has to start from the Sat/Sun
 * default to stay hydration-safe.
 */
export function useWeekendDays(): readonly number[] {
  const [days, setDays] = useState<readonly number[]>(DEFAULT_WEEKEND_PRESET.days);
  useEffect(() => setDays(weekendDaysFor(config.getWeekend())), []);
  return days;
}
