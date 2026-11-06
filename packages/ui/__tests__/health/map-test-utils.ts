/**
 * Shared harness for the canvas map tests. jsdom has no layout engine and no
 * canvas, so the map is given a fixed box and a null 2D context (the painter
 * skips cleanly), and a node is found on screen through the same pure layout
 * and fitted camera the component uses.
 */
import { act } from "@testing-library/react";
import { vi } from "vitest";
import {
  deriveAreaIndex,
  fitCamera,
  layoutAreas,
  layoutFiles,
  type CodeHealthMapFile,
} from "../../src/health/code-health-map.js";

/** The stub measures 800 x 600; the map's 640px minHeight wins the height. */
export const W = 800;
export const H = 640;

export function stubMapEnvironment(): void {
  class RO {
    cb: ResizeObserverCallback;
    constructor(cb: ResizeObserverCallback) {
      this.cb = cb;
    }
    observe() {
      this.cb(
        [{ contentRect: { width: W, height: 600 } } as ResizeObserverEntry],
        this as unknown as ResizeObserver,
      );
    }
    unobserve() {}
    disconnect() {}
  }
  vi.stubGlobal("ResizeObserver", RO);
  HTMLCanvasElement.prototype.getContext = (() =>
    null) as unknown as typeof HTMLCanvasElement.prototype.getContext;
}

/** Let the frame loop run once, so the field it decides is there to pick from. */
export async function nextFrame(): Promise<void> {
  await act(() => new Promise<void>((resolve) => requestAnimationFrame(() => resolve())));
}

/** Where a file's node lands on screen at the fitted camera. */
export function nodeAt(files: CodeHealthMapFile[], path: string) {
  const cam = fitCamera(W, H);
  const index = deriveAreaIndex(files);
  const { nodes } = layoutFiles(files, index, layoutAreas(index));
  const n = nodes.find((x) => x.file.file_path === path);
  if (!n) throw new Error(`no node for ${path}`);
  return { clientX: W / 2 + (n.x - cam.cx) * cam.scale, clientY: H / 2 + (n.y - cam.cy) * cam.scale };
}

export function mapFile(
  file_path: string,
  nloc: number,
  module: string | null,
  extra: Partial<CodeHealthMapFile> = {},
): CodeHealthMapFile {
  return {
    file_path,
    nloc,
    score: 7,
    module,
    line_coverage_pct: null,
    has_test_file: false,
    ...extra,
  };
}
