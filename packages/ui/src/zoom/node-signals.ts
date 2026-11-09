/**
 * What a card says about itself, in words. Pure, browser-free.
 *
 * The card draws its roles as icons and its health as a coloured number; the
 * hover card and the detail sheet say the same in words, and add on-flow,
 * which nearly every file is and so never earns a mark on the card.
 *
 * Every applicable role is named, not the winner of a priority cascade: a box
 * that is both an entry point and a hotspot is both.
 */

import { bandForScore, HEALTH_BAND_LABEL } from "@repowise-dev/types/health";
import type { ZoomKind, ZoomNode } from "./types";

/**
 * Human label per node kind.
 *
 * One record, because two surfaces name the same five kinds: the hover tooltip
 * over a card and the detail rail beside it. A reader moving between them is
 * looking at one node, so a divergence here would read as two different
 * things. The card itself shows the kind glyph, not the word.
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
 * carries none.
 *
 * A container inherits a role from its subtree (`metrics.*_count`), matching
 * the counts in the card's footer, so the words and the icons never disagree.
 */
export function nodeRoles(node: ZoomNode): string[] {
  const roles: string[] = [];
  if (node.is_entry_point || node.metrics.entry_point_count > 0) roles.push("Entry point");
  if (node.is_hotspot || node.metrics.hotspot_count > 0) roles.push("Hotspot");
  if (node.is_dead || node.metrics.dead_count > 0) roles.push("Dead code");
  if (node.on_flow || node.metrics.on_flow_count > 0) roles.push("On an execution flow");
  return roles;
}

/**
 * The health score's band as a word, on the same `bandForScore` the card's
 * corner number is coloured by, so the label never contradicts the colour.
 */
export function healthBandLabel(score: number | null): string | null {
  if (score === null) return null;
  return HEALTH_BAND_LABEL[bandForScore(score)];
}
