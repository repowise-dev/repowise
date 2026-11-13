"use client";

import { useEffect, useRef } from "react";
import { ScrollArea } from "../ui/scroll-area";
import { cn } from "../lib/cn";

export interface JobLogEntry {
  text: string;
  /** Pipeline message level ("info", "warning", "error"); tints the line. */
  level?: string | number;
}

interface JobLogProps {
  entries: JobLogEntry[];
  maxLines?: number;
}

function levelClass(level: JobLogEntry["level"]): string {
  if (level === "error") return "text-[var(--color-outdated)]";
  if (level === "warning") return "text-[var(--color-warning)]";
  return "text-[var(--color-text-tertiary)]";
}

export function JobLog({ entries, maxLines = 6 }: JobLogProps) {
  const viewportRef = useRef<HTMLDivElement>(null);

  // Follow the newest line by scrolling the log's own viewport. `scrollIntoView`
  // also scrolls every scrollable ancestor, so each new line dragged the whole
  // page back to the log while a job ran (#5469).
  useEffect(() => {
    const viewport = viewportRef.current;
    if (viewport) viewport.scrollTop = viewport.scrollHeight;
  }, [entries]);

  const visible = entries.slice(-maxLines);

  return (
    // Radix wraps the viewport's content in a `display:table` div that grows to
    // its widest line, so `truncate` below never applied and a long line
    // widened the whole panel. `block` holds the content to the panel's width.
    <ScrollArea
      className="h-28 rounded border border-[var(--color-border-default)] bg-[var(--color-bg-elevated)]"
      viewportRef={viewportRef}
      viewportClassName="[&>div]:block!"
    >
      <div className="p-2 space-y-0.5 font-mono text-xs">
        {visible.map((e, i) => (
          <div key={i} className={cn("leading-5 truncate", levelClass(e.level))}>
            <span className="text-[var(--color-accent-primary)] mr-1">›</span>
            {e.text}
          </div>
        ))}
      </div>
    </ScrollArea>
  );
}
