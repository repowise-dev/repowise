"use client";

/**
 * The keyboard model: one tab stop reaches the whole field. Arrows move over
 * what is inside the selected area (its sub-areas, then its own files once
 * they are loaded), Enter opens the area or the file under the cursor,
 * Backspace climbs one level and Escape returns to the repository. The
 * alternative is a tab stop per node, which is thousands.
 *
 * Escape and Backspace are consumed only while the focus is inside the field
 * and there is somewhere to climb to: the map listens on the window in the
 * capture phase and stops the key there, so one press never both climbs and
 * closes something behind the map. A key the map has no use for passes on.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { KeyboardEvent as ReactKeyboardEvent, RefObject } from "react";

import { ROOT_AREA } from "../../lib/areas";
import type { AreaIndex } from "./areas";
import { displayChildren, displayParent } from "./area-paths";
import type { FieldScene } from "./paint";
import { ZOOM_STEP, type MapCamera } from "./use-map-camera";

export type CursorItem = { kind: "area"; path: string } | { kind: "file"; path: string };

/** Step `i` forward or back around a ring of `n`. */
const cycle = (i: number, n: number, forward: boolean) =>
  n === 0 ? 0 : (i + (forward ? 1 : n - 1)) % n;

/** What the cursor walks inside `area`: sub-areas biggest first, then own files. */
export function cursorItems(index: AreaIndex, scene: FieldScene, area: string): CursorItem[] {
  const items: CursorItem[] = displayChildren(index, area).map((path) => ({ kind: "area", path }));
  const host = scene.hosts.find((h) => h.area === area && h.direct);
  if (host) for (const i of host.nodes) items.push({ kind: "file", path: scene.nodes[i]!.file.file_path });
  return items;
}

/** Where the keyboard cursor is, and whether it is in use. */
export function useKeyboardCursor(index: AreaIndex, scene: FieldScene, area: string) {
  const items = useMemo(() => cursorItems(index, scene, area), [index, scene, area]);
  const [pos, setPos] = useState(0);
  // Drawn only while the field has keyboard focus, so a reader using a
  // pointer never sees a ring that answers to keys they are not using.
  const [focused, setFocused] = useState(false);
  useEffect(() => setPos(0), [area]);
  const item = items[Math.min(pos, items.length - 1)] ?? null;
  return { items, item, setPos, focused, setFocused };
}

export type KeyboardCursor = ReturnType<typeof useKeyboardCursor>;

const hasChord = (e: { ctrlKey: boolean; metaKey: boolean; altKey: boolean }) => e.ctrlKey || e.metaKey || e.altKey;

/** What one key does in the field, or null when it is not the map's. Pure. */
export type KeyAction =
  | { kind: "zoom"; factor: number }
  | { kind: "root" }
  | { kind: "move"; forward: boolean }
  | { kind: "open" };

const KEY_ACTIONS: Record<string, KeyAction> = {
  "+": { kind: "zoom", factor: ZOOM_STEP },
  "=": { kind: "zoom", factor: ZOOM_STEP },
  "-": { kind: "zoom", factor: 1 / ZOOM_STEP },
  _: { kind: "zoom", factor: 1 / ZOOM_STEP },
  "0": { kind: "root" },
  ArrowRight: { kind: "move", forward: true },
  ArrowDown: { kind: "move", forward: true },
  ArrowLeft: { kind: "move", forward: false },
  ArrowUp: { kind: "move", forward: false },
  Enter: { kind: "open" },
};

/** Browser zoom and other chords stay the browser's. */
export function keyAction(e: { key: string; ctrlKey: boolean; metaKey: boolean; altKey: boolean }): KeyAction | null {
  return hasChord(e) ? null : (KEY_ACTIONS[e.key] ?? null);
}

/** Escape and Backspace climb, but only from inside the field and never out of a text box. */
function climbKey(e: KeyboardEvent, container: HTMLElement | null): "root" | "up" | null {
  if ((e.key !== "Escape" && e.key !== "Backspace") || hasChord(e)) return null;
  const t = e.target;
  if (!container || !(t instanceof Node) || !container.contains(t)) return null;
  if (t instanceof HTMLInputElement || t instanceof HTMLTextAreaElement) return null;
  return e.key === "Escape" ? "root" : "up";
}

/**
 * Escape returns to the repository and Backspace climbs one level, consumed
 * in the capture phase so one press never also closes something behind.
 */
function useClimbKeys(
  container: RefObject<HTMLElement | null>,
  index: AreaIndex,
  area: string,
  onSelectArea: (path: string) => void,
) {
  const climb = useRef({ area, parent: displayParent(index, area), select: onSelectArea });
  climb.current = { area, parent: displayParent(index, area), select: onSelectArea };
  useEffect(() => {
    if (typeof window === "undefined") return;
    const onKey = (e: KeyboardEvent) => {
      const which = climbKey(e, container.current);
      const { area: here, parent, select } = climb.current;
      if (!which || here === ROOT_AREA) return;
      e.stopPropagation();
      e.preventDefault();
      select(which === "root" ? ROOT_AREA : (parent ?? ROOT_AREA));
    };
    window.addEventListener("keydown", onKey, true);
    return () => window.removeEventListener("keydown", onKey, true);
  }, [container]);
}

export function useMapKeyboard(opts: {
  container: RefObject<HTMLElement | null>;
  kb: KeyboardCursor;
  index: AreaIndex;
  area: string;
  camera: MapCamera;
  onSelectArea: (path: string) => void;
  onSelectFile: (path: string) => void;
}) {
  const { container, kb, index, area, camera, onSelectArea, onSelectFile } = opts;
  useClimbKeys(container, index, area, onSelectArea);

  return useCallback(
    (e: ReactKeyboardEvent) => {
      const action = keyAction(e);
      if (!action || (action.kind === "open" && !kb.item)) return;
      e.preventDefault();
      if (action.kind === "zoom") camera.zoomBy(action.factor);
      else if (action.kind === "root") onSelectArea(ROOT_AREA);
      else if (action.kind === "move") {
        // A pointer-focused field shows the cursor once the keys are used.
        kb.setFocused(true);
        kb.setPos((i) => cycle(i, kb.items.length, action.forward));
      } else if (kb.item!.kind === "area") onSelectArea(kb.item!.path);
      else onSelectFile(kb.item!.path);
    },
    [camera, kb, onSelectArea, onSelectFile],
  );
}
