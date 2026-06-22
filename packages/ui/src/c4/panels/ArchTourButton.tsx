"use client";

import { useCallback, useEffect, useState } from "react";
import { Compass } from "lucide-react";
import { useArchitectureStore } from "../store/use-architecture-store";

// First-visit discoverability flag: the pill stays highlighted until the tour
// has been taken once on this device.
const TOUR_SEEN_KEY = "repowise:arch-tour-seen";

/**
 * "Take the tour" trigger for the layered architecture view.
 *
 * Promoted out of the per-app host so both apps get the same walkthrough from
 * one source: it reads the curated tour off the shared architecture store and
 * drives the store's tour player. Renders nothing when the view has no tour
 * steps (or while a tour is already running), so a host whose view carries no
 * tour degrades to no button rather than erroring — the host just mounts it
 * unconditionally next to the other view controls.
 */
export function ArchTourButton() {
  const view = useArchitectureStore((s) => s.view);
  const tourActive = useArchitectureStore((s) => s.tourActive);
  const startTour = useArchitectureStore((s) => s.startTour);

  // Highlighted until first taken (localStorage flag). Defaults to "seen" so the
  // accent treatment never flashes before the flag is read on the client.
  const [tourSeen, setTourSeen] = useState(true);
  useEffect(() => {
    setTourSeen(localStorage.getItem(TOUR_SEEN_KEY) === "1");
  }, []);

  const handleStartTour = useCallback(() => {
    localStorage.setItem(TOUR_SEEN_KEY, "1");
    setTourSeen(true);
    startTour();
  }, [startTour]);

  if (!view || view.tour.length === 0 || tourActive) return null;

  return (
    <button
      onClick={handleStartTour}
      className={`flex items-center gap-1.5 rounded-full border px-3 py-1 text-xs font-medium transition-colors ${
        tourSeen
          ? "border-[var(--color-border-default)] text-[var(--color-text-secondary)] hover:text-[var(--color-text-primary)] hover:bg-[var(--color-bg-elevated)]"
          : "border-[var(--color-accent-primary)]/50 bg-[var(--color-accent-primary)]/10 text-[var(--color-accent-primary)] hover:bg-[var(--color-accent-primary)]/20"
      }`}
    >
      <Compass className="h-3.5 w-3.5" />
      Take the tour
    </button>
  );
}
