import { useEffect, type Dispatch, type SetStateAction } from "react";
import type { ColorMode } from "./graph-toolbar";
import type { SigmaCanvasHandle } from "./sigma/sigma-canvas";
import type { GraphCtxMenu } from "./use-graph-context-menu";

interface GraphKeyboardShortcutOptions {
  sigmaRef: { current: SigmaCanvasHandle | null };
  setSelectedNodeId: Dispatch<SetStateAction<string | null>>;
  setEgoDepth: Dispatch<SetStateAction<number>>;
  setSearchQuery: Dispatch<SetStateAction<string>>;
  setCtxMenu: Dispatch<SetStateAction<GraphCtxMenu | null>>;
  setCommunityPanelId: Dispatch<SetStateAction<number | null>>;
  setColorMode: Dispatch<SetStateAction<ColorMode>>;
  /** Optional Escape pre-handler. Return true to consume the keystroke and skip
   *  the default clear. Used to dismiss the top UI layer first: clear an open
   *  selection/panel, else collapse the most recent constellation hub. */
  onEscape?: (() => boolean) | undefined;
}

/**
 * Global keyboard shortcuts for the graph view: `f` fit, `Escape` clears,
 * `1/2/3` switch color mode, `/` and `cmd/ctrl+k` focus search. Typing in an
 * input / textarea / contenteditable is ignored.
 */
export function useGraphKeyboardShortcuts(opts: GraphKeyboardShortcutOptions): void {
  const {
    sigmaRef,
    setSelectedNodeId,
    setEgoDepth,
    setSearchQuery,
    setCtxMenu,
    setCommunityPanelId,
    setColorMode,
    onEscape,
  } = opts;

  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      const target = e.target as HTMLElement;
      if (target.tagName === "INPUT" || target.tagName === "TEXTAREA" || target.isContentEditable)
        return;

      switch (e.key) {
        case "f":
          e.preventDefault();
          sigmaRef.current?.fitView();
          break;
        case "Escape":
          // Let the host peel the top UI layer first (selection/panel, then
          // hub); only fall through to the blanket clear if nothing was open.
          if (onEscape?.()) {
            e.preventDefault();
            break;
          }
          setSelectedNodeId(null);
          setEgoDepth(0);
          setSearchQuery("");
          setCtxMenu(null);
          setCommunityPanelId(null);
          break;
        case "1":
          setColorMode("language");
          break;
        case "2":
          setColorMode("community");
          break;
        case "3":
          setColorMode("risk");
          break;
        case "/":
          e.preventDefault();
          document
            .querySelector<HTMLInputElement>('[aria-label="Search graph nodes"]')
            ?.focus();
          break;
      }

      if ((e.metaKey || e.ctrlKey) && e.key === "k") {
        e.preventDefault();
        document
          .querySelector<HTMLInputElement>('[aria-label="Search graph nodes"]')
          ?.focus();
      }
    };

    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
    // Setters and the ref object are stable identities, so this binds once.
  }, [
    sigmaRef,
    setSelectedNodeId,
    setEgoDepth,
    setSearchQuery,
    setCtxMenu,
    setCommunityPanelId,
    setColorMode,
    onEscape,
  ]);
}
