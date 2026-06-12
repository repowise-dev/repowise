"use client";

import { X } from "lucide-react";

const SHORTCUTS: { keys: string[]; action: string }[] = [
  { keys: ["f"], action: "Fit graph to view" },
  { keys: ["/"], action: "Focus search" },
  { keys: ["⌘K"], action: "Focus search" },
  { keys: ["1"], action: "Color by language" },
  { keys: ["2"], action: "Color by community" },
  { keys: ["3"], action: "Color by risk" },
  { keys: ["Esc"], action: "Dismiss top layer (panel, selection, blossom…)" },
  { keys: ["?"], action: "Toggle this help" },
];

const GESTURES: { gesture: string; action: string }[] = [
  { gesture: "Click", action: "Select + inspect a node" },
  { gesture: "Double-click", action: "Drill deeper (expand module / blossom hub / open doc)" },
  { gesture: "Right-click", action: "Context menu (docs, explore, path from/to)" },
];

/** Keyboard + gesture cheatsheet, toggled with `?`. */
export function GraphShortcutHelp({ onClose }: { onClose: () => void }) {
  return (
    <div className="absolute inset-0 z-20 flex items-center justify-center bg-black/40 p-4" onClick={onClose}>
      <div
        className="w-full max-w-sm rounded-lg border border-[var(--color-border-default)] bg-[var(--color-bg-elevated)] p-4 shadow-xl shadow-black/30"
        onClick={(e) => e.stopPropagation()}
        role="dialog"
        aria-label="Graph keyboard shortcuts"
      >
        <div className="mb-3 flex items-center justify-between">
          <h3 className="text-sm font-semibold text-[var(--color-text-primary)]">
            Keyboard shortcuts
          </h3>
          <button
            onClick={onClose}
            aria-label="Close shortcut help"
            className="text-[var(--color-text-tertiary)] transition-colors hover:text-[var(--color-text-primary)]"
          >
            <X className="h-4 w-4" />
          </button>
        </div>
        <div className="space-y-1.5">
          {SHORTCUTS.map((s) => (
            <div key={s.keys.join("+") + s.action} className="flex items-center justify-between gap-3 text-xs">
              <span className="text-[var(--color-text-secondary)]">{s.action}</span>
              <span className="flex shrink-0 gap-1">
                {s.keys.map((k) => (
                  <kbd
                    key={k}
                    className="rounded border border-[var(--color-border-default)] bg-[var(--color-bg-inset)] px-1.5 py-0.5 font-mono text-[10px] text-[var(--color-text-primary)]"
                  >
                    {k}
                  </kbd>
                ))}
              </span>
            </div>
          ))}
        </div>
        <p className="mb-1.5 mt-3 text-[10px] font-medium uppercase tracking-wider text-[var(--color-text-tertiary)]">
          Mouse
        </p>
        <div className="space-y-1.5">
          {GESTURES.map((g) => (
            <div key={g.gesture} className="flex items-start justify-between gap-3 text-xs">
              <span className="shrink-0 text-[var(--color-text-primary)]">{g.gesture}</span>
              <span className="text-right text-[var(--color-text-secondary)]">{g.action}</span>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
