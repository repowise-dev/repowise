"use client";

import type { C4Level } from "../types";

interface TabDef {
  level: C4Level;
  label: string;
  hint: string;
}

const TABS: TabDef[] = [
  { level: 1, label: "L1 · Context", hint: "System + people + external systems" },
  { level: 2, label: "L2 · Containers", hint: "Packages / deployable units" },
  { level: 3, label: "L3 · Components", hint: "Sub-modules inside a container" },
];

export interface C4LevelTabsProps {
  level: C4Level;
  onLevelChange: (level: C4Level) => void;
  l3Enabled: boolean;
}

export function C4LevelTabs({ level, onLevelChange, l3Enabled }: C4LevelTabsProps) {
  return (
    <div role="tablist" aria-label="Diagram level" style={{ display: "flex", gap: 4 }}>
      {TABS.map((t) => {
        const disabled = t.level === 3 && !l3Enabled;
        const active = level === t.level;
        return (
          <button
            key={t.level}
            type="button"
            role="tab"
            aria-selected={active}
            disabled={disabled}
            onClick={() => onLevelChange(t.level)}
            title={disabled ? "Pick a container at L2 first" : t.hint}
            style={{
              padding: "5px 12px",
              fontSize: 12,
              fontWeight: 500,
              borderRadius: 6,
              border: "1px solid var(--color-border-default)",
              background: active
                ? "var(--color-accent-muted, rgba(245,149,32,0.15))"
                : "transparent",
              color: active
                ? "var(--color-accent-primary)"
                : "var(--color-text-secondary)",
              cursor: disabled ? "not-allowed" : "pointer",
              opacity: disabled ? 0.4 : 1,
            }}
          >
            {t.label}
          </button>
        );
      })}
    </div>
  );
}
