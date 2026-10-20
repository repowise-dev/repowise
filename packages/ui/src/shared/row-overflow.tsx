"use client";

import { useState, type ComponentType } from "react";
import { MoreHorizontal } from "lucide-react";

import { Button } from "../ui/button";
import { Popover, PopoverContent, PopoverTrigger } from "../ui/popover";

export interface RowOverflowItem {
  label: string;
  icon: ComponentType<{ className?: string; "aria-hidden"?: boolean | "true" }>;
  onSelect: () => void;
}

/**
 * A row's secondary verbs behind one named button. The row itself carries the
 * primary verb; everything here is a different kind of thing to do.
 */
export function RowOverflow({ label, items }: { label: string; items: RowOverflowItem[] }) {
  const [open, setOpen] = useState(false);
  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <Button
          size="sm"
          variant="ghost"
          className="h-7 w-7 px-0 text-[var(--color-text-tertiary)]"
          aria-label={label}
          onClick={(event) => event.stopPropagation()}
        >
          <MoreHorizontal className="h-4 w-4" />
        </Button>
      </PopoverTrigger>
      <PopoverContent align="end" className="w-60 p-1" onClick={(event) => event.stopPropagation()}>
        {items.map(({ label: itemLabel, icon: Icon, onSelect }) => (
          <button
            key={itemLabel}
            type="button"
            onClick={() => {
              setOpen(false);
              onSelect();
            }}
            className="flex w-full items-center gap-2 rounded px-2 py-1.5 text-left text-sm text-[var(--color-text-secondary)] hover:bg-[var(--color-bg-elevated)] hover:text-[var(--color-text-primary)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-accent-primary)]"
          >
            <Icon className="h-3.5 w-3.5 shrink-0" aria-hidden="true" />
            {itemLabel}
          </button>
        ))}
      </PopoverContent>
    </Popover>
  );
}
