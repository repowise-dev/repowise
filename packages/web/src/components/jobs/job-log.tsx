"use client";

import { useEffect, useRef } from "react";
import { ScrollArea } from "@repowise/ui/ui/scroll-area";

interface LogEntry {
  text: string;
  level?: number;
}

interface Props {
  entries: LogEntry[];
  maxLines?: number;
}

export function JobLog({ entries, maxLines = 6 }: Props) {
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [entries]);

  const visible = entries.slice(-maxLines);

  return (
    <ScrollArea className="h-28 rounded border border-[var(--color-border-default)] bg-[var(--color-bg-elevated)]">
      <div className="p-2 space-y-0.5 font-mono text-xs">
        {visible.map((e, i) => (
          <div key={i} className="text-[var(--color-text-tertiary)] leading-5 truncate">
            <span className="text-[var(--color-accent-primary)] mr-1">›</span>
            {e.text}
          </div>
        ))}
        <div ref={bottomRef} />
      </div>
    </ScrollArea>
  );
}
