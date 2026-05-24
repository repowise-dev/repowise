"use client";

import { useEffect } from "react";
import { useArchitectureStore } from "../store/use-architecture-store";

export function useArchitectureNavigation(): void {
  useEffect(() => {
    function handler(e: KeyboardEvent) {
      const t = e.target as HTMLElement | null;
      const tag = t?.tagName;
      if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT" || (t && t.isContentEditable)) {
        return;
      }

      const state = useArchitectureStore.getState();

      switch (e.key) {
        case "Escape": {
          if (state.tourActive) {
            state.endTour();
          } else if (state.codeViewerOpen) {
            state.closeCodeViewer();
          } else if (state.selectedNodeId !== null) {
            state.selectNode(null);
          } else if (state.navigationLevel === "layer-detail") {
            state.drillOut();
          }
          break;
        }
        case "ArrowLeft": {
          if (state.tourActive) {
            e.preventDefault();
            state.prevTourStep();
          }
          break;
        }
        case "ArrowRight": {
          if (state.tourActive) {
            e.preventDefault();
            state.nextTourStep();
          }
          break;
        }
        case "/": {
          e.preventDefault();
          window.dispatchEvent(new CustomEvent("arch:focus-search"));
          break;
        }
        case "f":
        case "F": {
          if (!e.ctrlKey && !e.metaKey) {
            state.setFilterPanelOpen(!state.filterPanelOpen);
          }
          break;
        }
        case "p":
        case "P": {
          if (!e.ctrlKey && !e.metaKey) {
            state.setPathFinderOpen(!state.pathFinderOpen);
          }
          break;
        }
      }
    }

    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, []);
}
