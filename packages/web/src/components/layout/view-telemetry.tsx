"use client";

import { useEffect } from "react";
import { usePathname } from "next/navigation";
import { recordViewOpened } from "@/lib/api/telemetry";
import { viewIdFor } from "./nav-items";

/**
 * Reports each nav view as it is opened: once per entry into a view, not per
 * page inside it (a file under Files is still `repo.files`).
 */
export function ViewTelemetry() {
  const view = viewIdFor(usePathname() ?? "");
  useEffect(() => {
    if (view) recordViewOpened(view);
  }, [view]);
  return null;
}
