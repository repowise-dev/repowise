/**
 * What a card says about itself, in words. Pure, browser-free.
 *
 * A card carries two dots and nothing on its surface names either, so the role
 * dot is a single accent dot meaning "there is something here" and the band
 * colours are left to health. These are the words that say which roles.
 *
 * Every applicable role is named, not the winner of a priority cascade: a box
 * that is both an entry point and a hotspot is both.
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
 * The health dot's band as a word, on the same `bandForScore` the dot itself
 * paints on, so the label never contradicts the mark it sits beside.
 */
export function healthBandLabel(score: number | null): string | null {
  if (score === null) return null;
  return HEALTH_BAND_LABEL[bandForScore(score)];
}
