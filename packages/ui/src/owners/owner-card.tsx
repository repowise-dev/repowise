"use client";

import { Flame, ShieldAlert, Trash2, GitCommit } from "lucide-react";
import type { OwnerListEntry } from "@repowise-dev/types/owners";
import { Card } from "../ui/card";
import { Badge } from "../ui/badge";
import { cn } from "../lib/cn";
import { formatRelativeTimeOrNull } from "../lib/format";
import { OwnerAvatar } from "./owner-avatar";

interface OwnerCardProps {
  owner: OwnerListEntry;
  onSelect?: (owner: OwnerListEntry) => void;
  highlight?: boolean;
}

/**
 * Single-row owner card used in the directory list. Highlights silos and
 * bus-factor risk so engineering leaders can spot concentrated risk fast.
 */
export function OwnerCard({ owner, onSelect, highlight }: OwnerCardProps) {
  const risky = owner.bus_factor_risk_files > 0 || owner.silo_modules > 0;
  return (
    <Card
      onClick={() => onSelect?.(owner)}
      className={cn(
        "group cursor-pointer p-4 transition-all hover:border-[var(--color-border-hover)] hover:shadow-[0_0_0_1px_var(--color-border-hover)]",
        highlight && "ring-1 ring-[var(--color-accent-primary)]/40",
      )}
    >
      <div className="flex items-start gap-3">
        <OwnerAvatar name={owner.name} email={owner.email} size="md" />
        <div className="min-w-0 flex-1">
          <div className="flex items-center justify-between gap-2">
            <div className="truncate text-sm font-medium text-[var(--color-text-primary)]">
              {owner.name}
            </div>
            {risky && (
              <Badge variant="outdated" className="shrink-0 text-[10px]">
                risk
              </Badge>
            )}
          </div>
          {owner.email && (
            <div className="truncate text-xs text-[var(--color-text-tertiary)]">
              {owner.email}
            </div>
          )}
          <div className="mt-3 grid grid-cols-4 gap-2 text-xs">
            <Stat label="Files" value={owner.files_owned} />
            <Stat
              label="Hotspots"
              value={owner.hotspots_owned}
              icon={<Flame className="h-3 w-3 text-[var(--color-warning)]" />}
              tone={owner.hotspots_owned > 0 ? "warn" : undefined}
            />
            <Stat
              label="Bus≤1"
              value={owner.bus_factor_risk_files}
              icon={<ShieldAlert className="h-3 w-3 text-[var(--color-error)]" />}
              tone={owner.bus_factor_risk_files > 0 ? "danger" : undefined}
            />
            <Stat
              label="Dead lines"
              value={owner.dead_code_lines_owned}
              icon={<Trash2 className="h-3 w-3 text-[var(--color-text-tertiary)]" />}
              tone={owner.dead_code_lines_owned > 0 ? "muted" : undefined}
            />
          </div>
          <div className="mt-3 flex items-center justify-between text-xs text-[var(--color-text-tertiary)]">
            <span className="flex items-center gap-1">
              <GitCommit className="h-3 w-3" />
              {owner.commit_count_90d} commits / 90d
            </span>
            <span>last touched {formatRelativeTimeOrNull(owner.last_commit_at)}</span>
          </div>
        </div>
      </div>
    </Card>
  );
}

function Stat({
  label,
  value,
  icon,
  tone,
}: {
  label: string;
  value: number;
  icon?: React.ReactNode | undefined;
  tone?: "warn" | "danger" | "muted" | undefined;
}) {
  const toneCls =
    tone === "danger"
      ? "text-[var(--color-error)]"
      : tone === "warn"
        ? "text-[var(--color-warning)]"
        : tone === "muted"
          ? "text-[var(--color-text-tertiary)]"
          : "text-[var(--color-text-primary)]";
  return (
    <div>
      <div className="flex items-center gap-1 text-[10px] uppercase tracking-wider text-[var(--color-text-tertiary)]">
        {icon}
        {label}
      </div>
      <div className={cn("tabular-nums font-semibold", toneCls)}>{value}</div>
    </div>
  );
}
