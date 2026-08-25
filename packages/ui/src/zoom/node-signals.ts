/**
 * What a card says about itself, in words. Pure, browser-free.
 *
 * A card carries two dots and nothing on the surface named either. Worse, they
 * shared a palette: the role dot painted `--color-success` for "has an entry
 * point" while the health dot painted the same token for "healthy", so a reader
 * who worked out one dot would misread the other. Colour bands belong to health
 * (one accent, two semantics), so the role dot is now a single accent dot
 * meaning "there is something here" and this module supplies the words that say
 * what.
 *
 * Naming all applicable roles also fixes a silent drop: the old dot ran a
 * priority cascade (entry > hotspot > dead > on-flow) and drew only the winner,
 * so a box that was both an entry point and a hotspot reported only "entry".
 */

import { bandForScore, HEALTH_BAND_LABEL } from "@repowise-dev/types/health";
import type { ZoomKind, ZoomNode } from "./types";

/**
 * Human label per node kind.
 *
 * One record, because three surfaces name the same five kinds: the card footer
 * the canvas draws, the hover tooltip over it, and the detail rail beside it. A
 * reader moving between them is looking at one node, so a divergence here would
 * read as three different things.
 */
export const KIND_LABEL: Record<ZoomKind, string> = {
  system: "System",
  layer: "Layer",
  group: "Group",
  folder: "Folder",
  file: "File",
};

/**
 * Every role that applies to a node, most notable first. Empty when the node
 * carries none, which is when the card draws no role dot at all.
 *
 * A container inherits a role from its subtree (`metrics.*_count`), matching
 * what the card's dot tests, so the words and the dot can never disagree.
 */
export function nodeRoles(node: ZoomNode): string[] {
  const roles: string[] = [];
  if (node.is_entry_point || node.metrics.entry_point_count > 0) roles.push("Entry point");
  if (node.is_hotspot || node.metrics.hotspot_count > 0) roles.push("Hotspot");
  if (node.is_dead || node.metrics.dead_count > 0) roles.push("Dead code");
  if (node.on_flow || node.metrics.on_flow_count > 0) roles.push("On an execution flow");
  return roles;
}

/** True when the card should draw its role dot. */
export function hasRole(node: ZoomNode): boolean {
  return nodeRoles(node).length > 0;
}

/**
 * The health dot's band as a word, from the canonical 3-band scale in
 * `@repowise-dev/types/health` — the same `bandForScore` the dot itself paints
 * on. Not the 5-step Excellent/Good/Fair ladder the scan surfaces use: a 6.9
 * reads "Good" there and paints amber here, and a label that contradicts the
 * dot it sits beside is worse than no label.
 */
export function healthBandLabel(score: number | null): string | null {
  if (score === null) return null;
  return HEALTH_BAND_LABEL[bandForScore(score)];
}
