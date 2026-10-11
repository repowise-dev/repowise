"use client";

import { Map, Info, FolderOpen } from "lucide-react";

export type MobileTab = "graph" | "info" | "files";

export interface MobileBottomNavProps {
  activeTab: MobileTab;
  onTabChange: (tab: MobileTab) => void;
}

const TABS: { key: MobileTab; label: string; Icon: typeof Map }[] = [
  { key: "graph", label: "Graph", Icon: Map },
  { key: "info", label: "Info", Icon: Info },
  { key: "files", label: "Files", Icon: FolderOpen },
];

export function MobileBottomNav({ activeTab, onTabChange }: MobileBottomNavProps) {
  return (
    <nav
      role="tablist"
      aria-label="Mobile navigation"
      style={{
        display: "flex",
        height: 56,
        borderTop: "1px solid var(--color-border-default)",
        background: "var(--color-bg-elevated, rgba(17,24,39,0.96))",
        flexShrink: 0,
      }}
    >
      {TABS.map(({ key, label, Icon }) => {
        const isActive = activeTab === key;
        return (
          <button
            key={key}
            type="button"
            role="tab"
            aria-selected={isActive}
            aria-label={`${label} tab`}
            onClick={() => onTabChange(key)}
            style={{
              flex: 1,
              display: "flex",
              flexDirection: "column",
              alignItems: "center",
              justifyContent: "center",
              gap: 2,
              minHeight: 44,
              minWidth: 44,
              background: "none",
              border: "none",
              cursor: "pointer",
              color: isActive
                ? "var(--color-accent-primary)"
                : "var(--color-text-secondary)",
              fontSize: 10,
              fontWeight: 600,
              letterSpacing: 0.3,
              outline: "none",
            }}
            onFocus={(e) => {
              e.currentTarget.style.outline = "2px solid var(--color-accent-primary)";
              e.currentTarget.style.outlineOffset = "-2px";
            }}
            onBlur={(e) => {
              e.currentTarget.style.outline = "none";
            }}
          >
            <Icon size={20} aria-hidden />
            {label}
          </button>
        );
      })}
    </nav>
  );
}
