import * as React from "react";
import { cn } from "../lib/cn";

export interface RowAction {
  icon: React.ComponentType<{ className?: string }>;
  label: string;
  href: string;
}

interface RowActionsProps {
  actions: RowAction[];
  className?: string;
}

export function RowActions({ actions, className }: RowActionsProps) {
  return (
    <div className={cn("flex items-center gap-1", className)}>
      {actions.map((action) => (
        <a
          key={action.label}
          href={action.href}
          title={action.label}
          className="inline-flex items-center gap-1 rounded px-1.5 py-1 text-[10px] text-[var(--color-text-tertiary)] hover:text-[var(--color-accent-primary)] hover:bg-[var(--color-bg-elevated)] transition-colors"
        >
          <action.icon className="h-3 w-3" />
          <span className="hidden lg:inline">{action.label}</span>
        </a>
      ))}
    </div>
  );
}
