"use client";

import { createContext, useContext, useMemo } from "react";
import {
  COMMUNITY_FAMILY_COUNT,
  useCommunityFamilies,
  type CommunityFamily,
} from "../shared/use-theme-tokens";

/**
 * Community colour keyed by something that survives a re-index.
 *
 * The family used to be `communityId % 12`. Community ids are size ranks
 * (`detect_file_communities` re-indexes by member count), so one community
 * growing past another renumbers both and every group on the map changes
 * colour between two indexes of the same code. A colour a reader has learned
 * is part of the map; it should move only when the group does.
 *
 * So the family is picked from the community's label, which is derived from
 * its paths and does not depend on rank. Larger groups choose first and take
 * their preferred family, or the next free one if a larger group already has
 * it, so the twelve biggest always get twelve distinct colours; past twelve,
 * groups share, as they always did.
 *
 * Ceiling: stable exactly as far as the label is. A community whose label
 * changes (it gained a new dominant folder) changes colour. The upgrade is a
 * persisted colour key on the index, which needs a wire field.
 */
export interface CommunityColorEntry {
  communityId: number;
  label: string;
  size: number;
}

function labelHash(label: string): number {
  // The dedup suffix (`tests/unit (288)`) is a member count and changes with
  // the index, so it is not part of the name.
  const key = label.replace(/\s*\(\d+\)\s*$/, "").trim().toLowerCase();
  let h = 5381;
  for (let i = 0; i < key.length; i++) h = ((h << 5) + h + key.charCodeAt(i)) | 0;
  return Math.abs(h);
}

/** Community id → family slot (0..11). */
export function communityColorSlots(
  entries: readonly CommunityColorEntry[],
): Map<number, number> {
  const slots = new Map<number, number>();
  const taken = new Set<number>();
  const ranked = [...entries].sort(
    (a, b) => b.size - a.size || a.label.localeCompare(b.label) || a.communityId - b.communityId,
  );
  for (const e of ranked) {
    if (slots.has(e.communityId)) continue;
    const preferred = labelHash(e.label) % COMMUNITY_FAMILY_COUNT;
    let slot = preferred;
    if (taken.size < COMMUNITY_FAMILY_COUNT) {
      while (taken.has(slot)) slot = (slot + 1) % COMMUNITY_FAMILY_COUNT;
      taken.add(slot);
    }
    slots.set(e.communityId, slot);
  }
  return slots;
}

/** Slot map for the graph below it. Null keeps the id-keyed default, so a
 *  surface rendered outside `GraphFlow` is unaffected. */
export const CommunityColorContext = createContext<Map<number, number> | null>(null);

/** `useCommunityFamilies`, keyed through the stable slots when a graph
 *  provides them. Every community colour in the graph module reads this, so
 *  the canvas, the key and the inspector cannot disagree. */
export function useGraphCommunityFamilies(): (communityId: number) => CommunityFamily {
  const families = useCommunityFamilies();
  const slots = useContext(CommunityColorContext);
  return useMemo(
    () => (slots ? (cid: number) => families(slots.get(cid) ?? cid) : families),
    [families, slots],
  );
}
