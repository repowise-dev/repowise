import { useEffect, useState } from "react";

export interface GraphCtxMenu {
  x: number;
  y: number;
  nodeId: string;
  nodeType: string;
}

/**
 * Right-click context-menu state for the graph, plus the dismiss-on-outside-
 * click / Escape behaviour. The action handlers (view docs, explore, path
 * from/to) stay in the parent because they reach into its other state; this
 * hook owns only the menu's open/close lifecycle.
 */
export function useGraphContextMenu() {
  const [ctxMenu, setCtxMenu] = useState<GraphCtxMenu | null>(null);

  useEffect(() => {
    if (!ctxMenu) return;
    const close = () => setCtxMenu(null);
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") close();
    };
    window.addEventListener("click", close);
    window.addEventListener("keydown", onKey);
    return () => {
      window.removeEventListener("click", close);
      window.removeEventListener("keydown", onKey);
    };
  }, [ctxMenu]);

  return { ctxMenu, setCtxMenu };
}
