/**
 * The sentence the map's live region says when the reader changes level:
 * "Ordering opened: 5 services", "Focused on ordering.api: 3 callers, 2
 * dependencies". It changes only when the level does, so a screen reader
 * hears each move once. Pure, no React.
 */

import type { LevelView } from "./levels";
import type { SystemMapNavCopy } from "./nav-copy";
import type { MapViewState } from "./view-state";
import { TRAY_OPEN_TOKEN } from "./view-state";

export interface AnnounceArgs {
  levelView: LevelView | null;
  view: MapViewState;
  /** The display the reader sees (the view's, or the phone default). */
  display: "map" | "list";
  services: number;
  /** Service picked with `b`, waiting for the second. */
  pendingFrom: string | null;
  name: (serviceId: string) => string;
  groupLabel: (groupId: string) => string;
  copy: SystemMapNavCopy;
}

function columnSize(view: LevelView, id: string): number {
  const col = view.focus?.columns.find((c) => c.id === id);
  return col ? col.entries.length + col.folded.length : 0;
}

/** The open container's sentence: the last opened group (or the tray) and its size. */
function groupSentence(v: LevelView, last: string, a: AnnounceArgs): string {
  const container = v.nodes.find((n) =>
    last === TRAY_OPEN_TOKEN ? n.type === "tray" : n.type === "group" && n.groupId === last,
  );
  const size = container && "serviceIds" in container ? container.serviceIds.length : 0;
  return a.copy.announceGroup(a.groupLabel(last), size);
}

/** The sentence for the level drawn. */
function levelSentence(v: LevelView, a: AnnounceArgs): string {
  const { view, copy } = a;
  if (v.level === "between" && view.between) return copy.announceBetween(a.name(view.between.a), a.name(view.between.b));
  if (v.level === "focus" && v.focus) {
    return copy.announceFocus(a.name(v.focus.focus), columnSize(v, "callers"), columnSize(v, "dependencies"));
  }
  const last = view.open[view.open.length - 1];
  if (v.level === "group" && last) return groupSentence(v, last, a);
  const groups = v.grouped ? v.nodes.filter((n) => n.type === "group").length : 0;
  return copy.announceEstate(a.services, groups);
}

export function levelAnnouncement(a: AnnounceArgs): string {
  if (a.pendingFrom) return a.copy.announcePick(a.name(a.pendingFrom));
  if (a.display === "list") return a.copy.announceList(a.services);
  return a.levelView ? levelSentence(a.levelView, a) : "";
}
