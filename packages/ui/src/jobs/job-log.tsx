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
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [entries]);

  const visible = entries.slice(-maxLines);

  return (
    <ScrollArea className="h-28 rounded border border-[var(--color-border-default)] bg-[var(--color-bg-elevated)]">
      <div className="p-2 space-y-0.5 font-mono text-xs">
        {visible.map((e, i) => (
          <div key={i} className={cn("leading-5 truncate", levelClass(e.level))}>
            <span className="text-[var(--color-accent-primary)] mr-1">›</span>
            {e.text}
          </div>
        ))}
        <div ref={bottomRef} />
      </div>
    </ScrollArea>
  );
}
