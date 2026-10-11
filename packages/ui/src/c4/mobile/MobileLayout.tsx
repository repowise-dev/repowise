"use client";

import { useState, useEffect, type ReactNode } from "react";
import { MobileBottomNav, type MobileTab } from "./MobileBottomNav";

export function useIsMobile(): boolean {
  const [isMobile, setIsMobile] = useState<boolean>(() => {
    if (typeof window === "undefined") return false;
    return window.matchMedia("(max-width: 768px)").matches;
  });

  useEffect(() => {
    const mql = window.matchMedia("(max-width: 768px)");
    const handler = (e: MediaQueryListEvent) => setIsMobile(e.matches);
    mql.addEventListener("change", handler);
    return () => mql.removeEventListener("change", handler);
  }, []);

  return isMobile;
}

export interface MobileLayoutProps {
  graphContent: ReactNode;
  infoContent: ReactNode;
  filesContent: ReactNode;
  liveAnnouncement?: string;
}

export function MobileLayout({
  graphContent,
  infoContent,
  filesContent,
  liveAnnouncement,
}: MobileLayoutProps) {
  const [activeTab, setActiveTab] = useState<MobileTab>("graph");

  return (
    <div
      style={{
        position: "relative",
        width: "100%",
        height: "100%",
        display: "flex",
        flexDirection: "column",
        overflow: "hidden",
        background: "var(--color-bg-canvas)",
      }}
    >
      <div
        aria-live="polite"
        aria-atomic="true"
        style={{
          position: "absolute",
          width: 1,
          height: 1,
          padding: 0,
          margin: -1,
          overflow: "hidden",
          clip: "rect(0,0,0,0)",
          whiteSpace: "nowrap",
          border: 0,
        }}
      >
        {liveAnnouncement}
      </div>

      <div style={{ flex: 1, position: "relative", minHeight: 0 }}>
        <div
          data-testid="mobile-tab-content-graph"
          style={{
            position: "absolute",
            inset: 0,
            display: activeTab === "graph" ? "block" : "none",
            pointerEvents: activeTab === "graph" ? "auto" : "none",
          }}
        >
          {graphContent}
        </div>

        <div
          data-testid="mobile-tab-content-info"
          style={{
            position: "absolute",
            inset: 0,
            display: activeTab === "info" ? "block" : "none",
            pointerEvents: activeTab === "info" ? "auto" : "none",
            overflowY: "auto",
            background: "var(--color-bg-elevated, rgba(17,24,39,0.96))",
            color: "var(--color-text-primary)",
          }}
        >
          {infoContent}
        </div>

        <div
          data-testid="mobile-tab-content-files"
          style={{
            position: "absolute",
            inset: 0,
            display: activeTab === "files" ? "block" : "none",
            pointerEvents: activeTab === "files" ? "auto" : "none",
            overflowY: "auto",
            background: "var(--color-bg-elevated, rgba(17,24,39,0.96))",
            color: "var(--color-text-primary)",
          }}
        >
          {filesContent}
        </div>
      </div>

      <MobileBottomNav activeTab={activeTab} onTabChange={setActiveTab} />
    </div>
  );
}
