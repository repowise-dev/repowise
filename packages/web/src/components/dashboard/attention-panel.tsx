import {
  AlertTriangle,
  Trash2,
  Users,
  FileWarning,
  Lightbulb,
} from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle } from "@repowise/ui/ui/card";
import { Badge } from "@repowise/ui/ui/badge";

import type { AttentionItem } from "@/lib/utils/health-score";
export type { AttentionItem };

const ICON_MAP = {
  stale_decision: AlertTriangle,
  knowledge_silo: Users,
  ungoverned_hotspot: FileWarning,
  dead_code: Trash2,
  proposed_decision: Lightbulb,
} as const;

const SEVERITY_COLORS = {
  high: "text-[var(--color-error)]",
  medium: "text-[var(--color-warning)]",
  low: "text-[var(--color-text-tertiary)]",
} as const;

const TYPE_LABELS = {
  stale_decision: "Stale Decision",
  knowledge_silo: "Knowledge Silo",
  ungoverned_hotspot: "Ungoverned Hotspot",
  dead_code: "Dead Code",
  proposed_decision: "Needs Review",
} as const;

interface AttentionPanelProps {
  items: AttentionItem[];
  repoId: string;
}

export function AttentionPanel({ items, repoId }: AttentionPanelProps) {
  if (items.length === 0) {
    return (
      <Card>
        <CardContent className="p-6 text-center">
          <p className="text-sm text-[var(--color-success)]">
            No attention items — codebase looks healthy
          </p>
        </CardContent>
      </Card>
    );
  }

  return (
    <Card>
      <CardHeader className="pb-2">
        <CardTitle className="text-sm flex items-center justify-between">
          <span className="flex items-center gap-2">
            <AlertTriangle className="h-4 w-4 text-[var(--color-warning)]" />
            Attention Needed
          </span>
          <Badge variant="outline" className="text-[10px] h-5 tabular-nums">
            {items.length}
          </Badge>
        </CardTitle>
      </CardHeader>
      <CardContent className="pt-0">
        <div className="space-y-1">
          {items.slice(0, 8).map((item) => {
            const Icon = ICON_MAP[item.type];
            const href = item.href ?? getDefaultHref(item, repoId);
            return (
              <a
                key={item.id}
                href={href}
                className="flex items-start gap-2.5 p-2 -mx-2 rounded-md hover:bg-[var(--color-bg-elevated)] transition-colors group"
              >
                <Icon
                  className={`h-3.5 w-3.5 mt-0.5 shrink-0 ${SEVERITY_COLORS[item.severity]}`}
                />
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-2">
                    <span className="text-xs font-medium text-[var(--color-text-primary)] truncate group-hover:text-[var(--color-accent-primary)] transition-colors">
                      {item.title}
                    </span>
                    <span className="text-[10px] text-[var(--color-text-tertiary)] shrink-0">
                      {TYPE_LABELS[item.type]}
                    </span>
                  </div>
                  <p className="text-[11px] text-[var(--color-text-tertiary)] truncate">
                    {item.description}
                  </p>
                </div>
              </a>
            );
          })}
          {items.length > 8 && (
            <p className="text-[11px] text-[var(--color-text-tertiary)] text-center pt-1">
              +{items.length - 8} more items
            </p>
          )}
        </div>
      </CardContent>
    </Card>
  );
}

function getDefaultHref(item: AttentionItem, repoId: string): string {
  switch (item.type) {
    case "stale_decision":
    case "proposed_decision":
      return `/repos/${repoId}/decisions`;
    case "knowledge_silo":
      return `/repos/${repoId}/ownership`;
    case "ungoverned_hotspot":
      return `/repos/${repoId}/hotspots`;
    case "dead_code":
      return `/repos/${repoId}/dead-code`;
    default:
      return `/repos/${repoId}`;
  }
}

