"use client";

import { useState } from "react";
import {
  AlertTriangle,
  CheckCircle2,
  Trash2,
  Users,
  FileWarning,
  Lightbulb,
} from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle } from "../ui/card";
import { Badge } from "../ui/badge";
import { stripMarkdown } from "../lib/format";
import { EmptyState } from "../shared/empty-state";
import { fileEntityPath } from "../shared/entity/routes";
import { AiPromptButton } from "../health/ai-prompt-button";
import { AiPromptModal } from "../health/ai-prompt-modal";
import { buildWorkQueueAiPrompt } from "../health/ai-prompt-builder";

export type AttentionItemType =
  | "stale_decision"
  | "knowledge_silo"
  | "ungoverned_hotspot"
  | "dead_code"
  | "proposed_decision";

export interface AttentionItem {
  id: string;
  type: AttentionItemType;
  title: string;
  description: string;
  severity: "high" | "medium" | "low";
  /** What the item points at — a decision id, file path, owner, … Used to
   *  deep-link straight to the offending entity. */
  target_id?: string;
  href?: string;
}

const ICON_MAP = {
  stale_decision: AlertTriangle,
  knowledge_silo: Users,
  ungoverned_hotspot: FileWarning,
  dead_code: Trash2,
  proposed_decision: Lightbulb,
} as const;

/**
 * Severity ramp for the row icons. Only `high` earns a hue — a column of
 * saturated triangles reads as uniform noise and stops meaning "urgent". The
 * lower two steps encode severity by *emphasis* (primary vs tertiary text)
 * rather than colour, so all three stay distinguishable while red keeps its
 * meaning by being rare.
 */
const SEVERITY_COLORS = {
  high: "text-[var(--color-error)]",
  medium: "text-[var(--color-text-secondary)]",
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
  linkPrefix?: string;
  /** Initial preview window; expanding the panel reveals all items. */
  previewCount?: number;
  /** Shown next to the title of the generated work-queue prompt. */
  repoName?: string;
}

function getDefaultHref(item: AttentionItem, prefix: string): string {
  const target = item.target_id;
  switch (item.type) {
    case "stale_decision":
    case "proposed_decision":
      // Deep-link to the specific decision when we know which one.
      return target
        ? `${prefix}/decisions/${encodeURIComponent(target)}`
        : `${prefix}/decisions`;
    case "knowledge_silo":
      // The silo target is the owning module/path — surface it on the owners
      // view so the offending area is preselected.
      return target
        ? `${prefix}/owners?path=${encodeURIComponent(target)}`
        : `${prefix}/owners`;
    case "ungoverned_hotspot":
      // Target is the hotspot's file path → open its file entity page.
      return target ? fileEntityPath(prefix, target) : `${prefix}/code-health?tab=hotspots`;
    case "dead_code":
      return `${prefix}/code-health?tab=dead-code`;
    default:
      return prefix;
  }
}

export function AttentionPanel({
  items,
  repoId,
  linkPrefix,
  previewCount = 8,
  repoName,
}: AttentionPanelProps) {
  const prefix = linkPrefix ?? `/repos/${repoId}`;
  const [expanded, setExpanded] = useState(false);
  const [promptOpen, setPromptOpen] = useState(false);
  const visible = expanded ? items : items.slice(0, previewCount);
  if (items.length === 0) {
    return (
      <Card>
        <CardContent className="p-2">
          <EmptyState
            icon={<CheckCircle2 className="h-8 w-8 text-[var(--color-success)]" />}
            title="Nothing needs attention"
            description="No hotspots, risky files, or stale docs right now."
          />
        </CardContent>
      </Card>
    );
  }

  return (
    <>
    <Card>
      <CardHeader className="pb-2">
        <CardTitle className="text-sm flex items-center justify-between gap-2">
          <span className="flex items-center gap-2">
            <AlertTriangle className="h-4 w-4 text-[var(--color-warning)]" />
            Attention Needed
          </span>
          <span className="flex items-center gap-2">
            <AiPromptButton
              label="Hand queue to AI"
              onClick={() => setPromptOpen(true)}
            />
            <Badge variant="outline" className="text-[10px] h-5 tabular-nums">
              {items.length}
            </Badge>
          </span>
        </CardTitle>
      </CardHeader>
      <CardContent className="pt-0">
        <div className="space-y-1">
          {visible.map((item) => {
            const Icon = ICON_MAP[item.type];
            const href = item.href ?? getDefaultHref(item, prefix);
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
                      {stripMarkdown(item.title)}
                    </span>
                    <span className="text-[10px] text-[var(--color-text-tertiary)] shrink-0">
                      {TYPE_LABELS[item.type]}
                    </span>
                  </div>
                  <p className="text-xs text-[var(--color-text-tertiary)] truncate">
                    {item.description}
                  </p>
                </div>
              </a>
            );
          })}
          {items.length > previewCount && (
            <button
              type="button"
              onClick={() => setExpanded((v) => !v)}
              className="block w-full text-xs text-[var(--color-accent-primary)] hover:underline text-center pt-1"
            >
              {expanded
                ? "Show fewer"
                : `+${items.length - previewCount} more items — show all`}
            </button>
          )}
        </div>
      </CardContent>
    </Card>
    <AiPromptModal
      open={promptOpen}
      onOpenChange={setPromptOpen}
      getPrompt={(flavor) =>
        buildWorkQueueAiPrompt({
          items: items.map((it) => ({
            type: it.type,
            title: it.title,
            description: it.description,
            severity: it.severity,
            target_id: it.target_id ?? null,
          })),
          flavor,
          ...(repoName ? { repoName } : {}),
        })
      }
      title="AI work queue"
      description="A ready-to-paste prompt that hands your AI agent this repo's Attention Needed backlog as a prioritized worklist."
    />
    </>
  );
}
