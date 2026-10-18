"use client";

/**
 * The keyboard model: one tab stop reaches the whole field. Arrows move over
 * modules, Enter descends into one, arrows then move over its files, Enter
 * selects one. The alternative is a tab stop per node, which is thousands.
 *
 * Escape climbs out of a focused galaxy (and the file level) while the focus
 * is inside the field, and only then. When it does that it is consumed: the
 * open file drawer listens on the document in the capture phase, so the map
 * listens on the window in the capture phase and stops the key there, or one
 * press would both zoom out and close the drawer. An Escape the map has no
 * use for passes through untouched.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import type { KeyboardEvent as ReactKeyboardEvent, RefObject } from "react";

import type { FileNode, Galaxy } from "./types";
import { ZOOM_STEP, type MapCamera } from "./use-map-camera";

export type KeyboardLevel = "module" | "file";

/** Step `i` forward or back around a ring of `n`. */
const cycle = (i: number, n: number, forward: boolean) =>
  n === 0 ? 0 : (i + (forward ? 1 : n - 1)) % n;

const at = <T,>(list: readonly T[], i: number): T | null =>
  list[Math.min(i, Math.max(0, list.length - 1))] ?? null;

/** Where the keyboard cursor is, and whether it is in use. */
export function useKeyboardCursor(galaxies: Galaxy[]) {
  const [level, setLevel] = useState<KeyboardLevel>("module");
  const [moduleIdx, setModuleIdx] = useState(0);
  const [fileIdx, setFileIdx] = useState(0);
  // Drawn only while the field has keyboard focus, so a reader using a
  // pointer never sees a ring that answers to keys they are not using.
  const [focused, setFocused] = useState(false);
  const galaxy = at(galaxies, moduleIdx);
  const node: FileNode | null = level === "file" && galaxy ? at(galaxy.nodes, fileIdx) : null;
  return { level, setLevel, moduleIdx, setModuleIdx, setFileIdx, focused, setFocused, galaxy, node };
}

export type KeyboardCursor = ReturnType<typeof useKeyboardCursor>;

/** The camera keys, shared by both levels. True when the key was one. */
function cameraKey(e: ReactKeyboardEvent, camera: MapCamera, kb: KeyboardCursor): boolean {
  if (e.key === "+" || e.key === "=") camera.zoomBy(ZOOM_STEP);
  else if (e.key === "-" || e.key === "_") camera.zoomBy(1 / ZOOM_STEP);
  else if (e.key === "0") {
    kb.setLevel("module");
    camera.fitAll();
  } else return false;
  e.preventDefault();
  return true;
}

export function useMapKeyboard(opts: {
  container: RefObject<HTMLElement | null>;
  kb: KeyboardCursor;
  galaxies: Galaxy[];
  camera: MapCamera;
  focusModule: string | null;
  setFocusModule: (module: string | null) => void;
  onSelect: (path: string) => void;
}) {
  const { container, kb, galaxies, camera, focusModule, setFocusModule, onSelect } = opts;

  const onModuleKey = useCallback(
    (e: ReactKeyboardEvent, forward: boolean, back: boolean) => {
      if (forward || back) {
        e.preventDefault();
        kb.setModuleIdx((i) => cycle(i, galaxies.length, forward));
      } else if (e.key === "Enter" && kb.galaxy) {
        e.preventDefault();
        kb.setLevel("file");
        kb.setFileIdx(0);
        camera.focusOn(kb.galaxy.module);
      }
    },
    [kb, galaxies.length, camera],
  );

  const onFileKey = useCallback(
    (e: ReactKeyboardEvent, forward: boolean, back: boolean) => {
      if (forward || back) {
        e.preventDefault();
        const n = kb.galaxy?.nodes.length ?? 0;
        kb.setFileIdx((i) => cycle(i, n, forward));
      } else if (e.key === "Enter" && kb.node) {
        e.preventDefault();
        onSelect(kb.node.file.file_path);
      } else if (e.key === "Backspace" || e.key === "Escape") {
        e.preventDefault();
        kb.setLevel("module");
        setFocusModule(null);
      }
    },
    [kb, onSelect, setFocusModule],
  );

  const onKeyDown = useCallback(
    (e: ReactKeyboardEvent) => {
      if (cameraKey(e, camera, kb)) return;
      const forward = e.key === "ArrowRight" || e.key === "ArrowDown";
      const back = e.key === "ArrowLeft" || e.key === "ArrowUp";
      if (kb.level === "module") onModuleKey(e, forward, back);
      else onFileKey(e, forward, back);
    },
    [camera, kb, onModuleKey, onFileKey],
  );

  const escape = useRef({ focused: false, fileLevel: false });
  escape.current = { focused: focusModule != null, fileLevel: kb.level === "file" };
  const { setLevel } = kb;
  useEffect(() => {
    if (typeof window === "undefined") return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "Escape") return;
      const node = container.current;
      if (!node || !(e.target instanceof Node) || !node.contains(e.target)) return;
      if (!escape.current.focused && !escape.current.fileLevel) return;
      e.stopPropagation();
      e.preventDefault();
      setLevel("module");
      setFocusModule(null);
    };
    window.addEventListener("keydown", onKey, true);
    return () => window.removeEventListener("keydown", onKey, true);
  }, [container, setLevel, setFocusModule]);

  return onKeyDown;
}
