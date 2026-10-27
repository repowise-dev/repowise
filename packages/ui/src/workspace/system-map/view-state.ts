/**
 * The map's level state as plain data: which groups are open, which service
 * is focused, which two services are compared, and which edge families are
 * drawn. A host keeps it in its URL (`open`, `focus`, `a`, `b`, `kinds`);
 * this module reads and writes those params and steps between levels, so
 * every host walks the levels the same way. The Map | List display rides
 * along as `view` (absent is the default). Pure, no React.
 */

import { DEFAULT_EDGE_FAMILIES, EDGE_FAMILY_ORDER, type EdgeFamily } from "./edge-families";
import type { LevelViewState } from "./level-types";

/** The token `open` uses for the "Not linked at runtime" tray. Group ids carry a `source:` prefix, so it cannot clash. */
export const TRAY_OPEN_TOKEN = "tray";

export interface MapViewState {
  /** Opened group ids in the order they were opened, plus `TRAY_OPEN_TOKEN` when the tray is open. */
  open: readonly string[];
  /** Service id at the centre of the focus level. */
  focus: string | null;
  /** Two service ids compared at the between level. */
  between: { a: string; b: string } | null;
  /** Families drawn; null is the default (Runtime and Data). */
  families: readonly EdgeFamily[] | null;
  /** Map or the services table; absent is the default (the map, the table on phones). */
  display?: MapDisplay;
}

export type MapDisplay = "map" | "list";

export const ESTATE_VIEW: MapViewState = { open: [], focus: null, between: null, families: null };

/** The URL params the map state owns. */
export const MAP_VIEW_PARAMS = ["open", "focus", "a", "b", "kinds", "view"] as const;

const KNOWN_FAMILIES: ReadonlySet<string> = new Set(EDGE_FAMILY_ORDER);

function list(value: string | null): string[] {
  if (!value) return [];
  // A Set keeps first-seen order and drops repeats.
  return [...new Set(value.split(",").map((v) => v.trim()).filter(Boolean))];
}

/**
 * State from URL params. Unknown family names are dropped; a `kinds` that
 * names none we know reads as the default rather than as "draw nothing".
 * `a` without `b` (or the same id twice) is no comparison.
 */
export function parseMapView(get: (key: string) => string | null): MapViewState {
  const families = list(get("kinds")).filter((f): f is EdgeFamily => KNOWN_FAMILIES.has(f));
  const a = get("a");
  const b = get("b");
  const display = get("view");
  return {
    open: list(get("open")),
    focus: get("focus") || null,
    between: a && b && a !== b ? { a, b } : null,
    families: families.length > 0 ? EDGE_FAMILY_ORDER.filter((f) => families.includes(f)) : null,
    ...(display === "map" || display === "list" ? { display } : {}),
  };
}

function sameFamilies(families: readonly EdgeFamily[], defaults: ReadonlySet<EdgeFamily>): boolean {
  return families.length === defaults.size && families.every((f) => defaults.has(f));
}

/** Write `state` into `params`, removing every map param it does not set, so the URL stays short at the estate. */
export function writeMapView(params: URLSearchParams, state: MapViewState): void {
  for (const key of MAP_VIEW_PARAMS) params.delete(key);
  if (state.open.length > 0) params.set("open", state.open.join(","));
  if (state.focus) params.set("focus", state.focus);
  if (state.between) {
    params.set("a", state.between.a);
    params.set("b", state.between.b);
  }
  if (state.families && !sameFamilies(state.families, DEFAULT_EDGE_FAMILIES)) {
    params.set("kinds", EDGE_FAMILY_ORDER.filter((f) => state.families!.includes(f)).join(","));
  }
  if (state.display) params.set("view", state.display);
}

/** The families a state draws, as the levels model takes them. */
export function drawnFamilies(state: MapViewState): ReadonlySet<EdgeFamily> {
  return state.families ? new Set(state.families) : DEFAULT_EDGE_FAMILIES;
}

/** What the levels model needs from the state. */
export function toLevelViewState(state: MapViewState): LevelViewState {
  return {
    open: state.open.filter((id) => id !== TRAY_OPEN_TOKEN),
    openTray: state.open.includes(TRAY_OPEN_TOKEN),
    focus: state.focus,
    between: state.between,
    families: drawnFamilies(state),
  };
}

/** Open a group (or the tray) in place; reopening one moves it to the end, so Escape closes it first. */
export function openContainer(state: MapViewState, id: string): MapViewState {
  return { ...state, open: [...state.open.filter((o) => o !== id), id] };
}

export function closeContainer(state: MapViewState, id: string): MapViewState {
  if (!state.open.includes(id)) return state;
  return { ...state, open: state.open.filter((o) => o !== id) };
}

/** Show or hide one family. The last drawn family stays: a map with no edges drawn is not a view anyone asks for. */
export function toggleFamily(state: MapViewState, family: EdgeFamily): MapViewState {
  const current = drawnFamilies(state);
  if (current.has(family) && current.size === 1) return state;
  const next = current.has(family) ? [...current].filter((f) => f !== family) : [...current, family];
  return { ...state, families: EDGE_FAMILY_ORDER.filter((f) => next.includes(f)) };
}

/** Draw `family` if it is hidden; the state itself when it is already drawn. */
export function showFamily(state: MapViewState, family: EdgeFamily): MapViewState {
  return drawnFamilies(state).has(family) ? state : toggleFamily(state, family);
}

/**
 * One level up, for Escape and the breadcrumb: leave Between, then Focus,
 * then close the last opened group. Null at the estate, where there is
 * nothing left to back out of.
 */
export function backOut(state: MapViewState): MapViewState | null {
  if (state.between) return { ...state, between: null };
  if (state.focus) return { ...state, focus: null };
  if (state.open.length > 0) return { ...state, open: state.open.slice(0, -1) };
  return null;
}

/** The estate with the same families drawn: the breadcrumb's root. */
export function toEstate(state: MapViewState): MapViewState {
  return { ...ESTATE_VIEW, families: state.families, ...(state.display ? { display: state.display } : {}) };
}

/** Focus one service (the focus level), leaving any comparison. */
export function focusService(state: MapViewState, id: string): MapViewState {
  return { ...state, focus: id, between: null };
}

/** Compare two services (the between level); the same id twice is no comparison. */
export function compareServices(state: MapViewState, a: string, b: string): MapViewState {
  return a === b ? state : { ...state, between: { a, b } };
}

/** Show the map or the services table. */
export function withDisplay(state: MapViewState, display: MapDisplay): MapViewState {
  return { ...state, display };
}

export function sameMapView(x: MapViewState, y: MapViewState): boolean {
  return (
    x.open.join(",") === y.open.join(",") &&
    x.focus === y.focus &&
    x.between?.a === y.between?.a &&
    x.between?.b === y.between?.b &&
    [...drawnFamilies(x)].sort().join(",") === [...drawnFamilies(y)].sort().join(",") &&
    x.display === y.display
  );
}
