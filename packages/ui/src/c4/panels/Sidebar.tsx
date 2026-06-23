"use client";

import { useEffect, useState } from "react";
import { Info, FolderOpen, PanelRightOpen, X } from "lucide-react";
import { useArchitectureStore } from "../store/use-architecture-store";
import { ProjectOverview } from "./ProjectOverview";
import { ArchNodeInfo } from "./ArchNodeInfo";
import type { ArchNodeInfoProps } from "./ArchNodeInfo";
import { LearnPanel } from "./LearnPanel";
import { FileExplorer } from "./FileExplorer";

export interface SidebarProps {
  renderDoc?: ((content: string) => React.ReactNode) | undefined;
  health?: ArchNodeInfoProps["health"] | undefined;
  contributors?: { name: string; files: number; pct?: number }[] | undefined;
  docContent?: string | null | undefined;
  onOpenInGraph?: ((path: string) => void) | undefined;
  onOpenDoc?: ((href: string) => void) | undefined;
}

type SidebarTab = "info" | "files";

const TAB_STYLE_BASE: React.CSSProperties = {
  flex: 1,
  display: "flex",
  alignItems: "center",
  justifyContent: "center",
  gap: 5,
  padding: "8px 0",
  fontSize: 11,
  fontWeight: 600,
  letterSpacing: 0.4,
  textTransform: "uppercase",
  cursor: "pointer",
  background: "none",
  border: "none",
  borderBottomWidth: 2,
  borderBottomStyle: "solid",
  borderBottomColor: "transparent",
  color: "var(--color-text-secondary)",
};

const TAB_STYLE_ACTIVE: React.CSSProperties = {
  ...TAB_STYLE_BASE,
  color: "var(--color-accent-primary)",
  borderBottomColor: "var(--color-accent-primary)",
};

export function Sidebar(props: SidebarProps) {
  const view = useArchitectureStore((s) => s.view);
  const selectedNodeId = useArchitectureStore((s) => s.selectedNodeId);
  const tourActive = useArchitectureStore((s) => s.tourActive);
  const sidebarOpen = useArchitectureStore((s) => s.sidebarOpen);
  const setSidebarOpen = useArchitectureStore((s) => s.setSidebarOpen);
  const [activeTab, setActiveTab] = useState<SidebarTab>("info");

  // Collapse on small screens at first mount — a 320px overlay swallows the
  // whole graph on a phone. Runs once; the user's manual toggle wins after.
  useEffect(() => {
    if (
      typeof window !== "undefined" &&
      window.matchMedia("(max-width: 767px)").matches
    ) {
      setSidebarOpen(false);
    }
  }, [setSidebarOpen]);

  if (!view) return null;

  // Collapsed: a small floating affordance to bring the panel back, kept in
  // the same corner so it reads as "the panel lives here".
  if (!sidebarOpen) {
    return (
      <button
        type="button"
        aria-label="Open Knowledge Graph panel"
        onClick={() => setSidebarOpen(true)}
        style={{
          position: "absolute",
          top: 12,
          right: 12,
          zIndex: 5,
          display: "flex",
          alignItems: "center",
          gap: 6,
          padding: "8px 10px",
          fontSize: 12,
          fontWeight: 600,
          cursor: "pointer",
          color: "var(--color-text-primary)",
          background: "var(--color-bg-elevated, rgba(17,24,39,0.96))",
          border: "1px solid var(--color-border-default)",
          borderRadius: 8,
          boxShadow: "0 10px 30px rgba(0,0,0,0.35)",
        }}
      >
        <PanelRightOpen size={14} />
        Details
      </button>
    );
  }

  return (
    <aside
      aria-label="Knowledge Graph sidebar"
      style={{
        position: "absolute",
        top: 12,
        right: 12,
        // Cap to the viewport on phones so the panel never overflows the
        // canvas edge-to-edge.
        width: "min(320px, calc(100vw - 24px))",
        maxHeight: "calc(100% - 24px)",
        background: "var(--color-bg-elevated, rgba(17,24,39,0.96))",
        border: "1px solid var(--color-border-default)",
        borderRadius: 8,
        color: "var(--color-text-primary)",
        fontSize: 12,
        zIndex: 5,
        display: "flex",
        flexDirection: "column",
        boxShadow: "0 10px 30px rgba(0,0,0,0.35)",
      }}
    >
      <nav
        role="tablist"
        aria-label="Sidebar tabs"
        style={{
          display: "flex",
          alignItems: "stretch",
          borderBottom: "1px solid var(--color-border-default)",
          flexShrink: 0,
        }}
      >
        <button
          type="button"
          role="tab"
          aria-selected={activeTab === "info"}
          aria-controls="sidebar-panel-info"
          aria-label="Info tab"
          onClick={() => setActiveTab("info")}
          style={activeTab === "info" ? TAB_STYLE_ACTIVE : TAB_STYLE_BASE}
        >
          <Info size={13} />
          Info
        </button>
        <button
          type="button"
          role="tab"
          aria-selected={activeTab === "files"}
          aria-controls="sidebar-panel-files"
          aria-label="Files tab"
          onClick={() => setActiveTab("files")}
          style={activeTab === "files" ? TAB_STYLE_ACTIVE : TAB_STYLE_BASE}
        >
          <FolderOpen size={13} />
          Files
        </button>
        <button
          type="button"
          aria-label="Close panel"
          onClick={() => setSidebarOpen(false)}
          style={{
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            width: 34,
            flexShrink: 0,
            cursor: "pointer",
            background: "none",
            border: "none",
            borderLeft: "1px solid var(--color-border-default)",
            color: "var(--color-text-secondary)",
          }}
        >
          <X size={14} />
        </button>
      </nav>

      <div style={{ flex: 1, overflowY: "auto", minHeight: 0 }}>
        {activeTab === "info" && (
          <div id="sidebar-panel-info" role="tabpanel">
            {tourActive && <LearnPanel />}
            {selectedNodeId ? (
              <ArchNodeInfo
                health={props.health}
                contributors={props.contributors}
                renderDoc={props.renderDoc}
                docContent={props.docContent}
                onOpenInGraph={props.onOpenInGraph}
                onOpenDoc={props.onOpenDoc}
              />
            ) : !tourActive ? (
              <ProjectOverview />
            ) : null}
          </div>
        )}
        {activeTab === "files" && (
          <div id="sidebar-panel-files" role="tabpanel">
            <FileExplorer />
          </div>
        )}
      </div>
    </aside>
  );
}
