"use client";

import { ChevronRight } from "lucide-react";
import type { C4Level } from "../types";

export interface C4BreadcrumbProps {
  level: C4Level;
  systemName: string;
  activeContainerPath: string | null;
  onNavigate: (level: C4Level) => void;
}

export function C4Breadcrumb({
  level,
  systemName,
  activeContainerPath,
  onNavigate,
}: C4BreadcrumbProps) {
  const segments: { label: string; onClick: () => void; current: boolean }[] = [
    { label: systemName, onClick: () => onNavigate(1), current: level === 1 },
  ];
  if (level >= 2) {
    segments.push({ label: "Containers", onClick: () => onNavigate(2), current: level === 2 });
  }
  if (level === 3 && activeContainerPath) {
    segments.push({ label: activeContainerPath, onClick: () => onNavigate(3), current: true });
  }
  return (
    <nav
      aria-label="Knowledge Graph breadcrumb"
      style={{ display: "flex", alignItems: "center", gap: 4, fontSize: 12 }}
    >
      {segments.map((seg, i) => (
        <span key={i} style={{ display: "inline-flex", alignItems: "center", gap: 4 }}>
          {i > 0 && (
            <ChevronRight size={12} aria-hidden style={{ opacity: 0.5 }} />
          )}
          <button
            type="button"
            onClick={seg.onClick}
            disabled={seg.current}
            style={{
              background: "none",
              border: "none",
              padding: 0,
              cursor: seg.current ? "default" : "pointer",
              color: seg.current
                ? "var(--color-text-primary)"
                : "var(--color-text-secondary)",
              fontWeight: seg.current ? 600 : 500,
            }}
          >
            {seg.label}
          </button>
        </span>
      ))}
    </nav>
  );
}
