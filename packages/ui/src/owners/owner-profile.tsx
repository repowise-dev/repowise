"use client";

import { useMemo } from "react";
import {
  Flame,
  ShieldAlert,
  Trash2,
  GitCommit,
  Calendar,
  Users,
  Folder,
  TrendingUp,
  Bot,
} from "lucide-react";
import type {
  OwnerProfile,
  OwnerFileEntry,
  OwnerModuleRollup,
  OwnerCoAuthor,
} from "@repowise-dev/types/owners";
import { Card, CardContent, CardHeader, CardTitle } from "../ui/card";
import { Badge } from "../ui/badge";
import { EmptyState } from "../shared/empty-state";
import {
  ResponsiveTable,
  type ResponsiveColumn,
} from "../shared/responsive-table";
import { cn } from "../lib/cn";
import { truncatePath, formatRelativeTimeOrNull } from "../lib/format";
import { AgentTierBar } from "../git/agent-tier-bar";
import { OwnerAvatar } from "./owner-avatar";

const dateFormatter = new Intl.DateTimeFormat(undefined, {
  year: "numeric",
  month: "short",
  day: "numeric",
});

function fmtDate(iso: string | null): string {
  if (!iso) return "—";
  const t = new Date(iso);
  return Number.isNaN(t.getTime()) ? "—" : dateFormatter.format(t);
}

function fmtCompact(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}k`;
  return n.toLocaleString();
}

const timeAgo = (iso: string | null) => formatRelativeTimeOrNull(iso, "never");

export interface OwnerProfileViewProps {
  owner: OwnerProfile;
  onSelectFile?: (filePath: string) => void;
  onSelectModule?: (modulePath: string) => void;
  onSelectCoAuthor?: (coAuthor: OwnerCoAuthor) => void;
  /** Optional renderer for the embedded blast-radius / activity preview. */
  rightRail?: React.ReactNode;
}

/**
 * Engineering-leader contributor profile. All data comes from the
 * /api/repos/{id}/owners/{key} endpoint — no client-side aggregation.
 *
 * Layout:
 *  - Header: avatar, headline metrics, time-on-codebase line
 *  - Risk strip: silos / bus-factor / dead-code / hotspots, color-coded
 *  - Two-column body: top files + modules (left), co-authors + categories (right)
 */
export function OwnerProfileView({
  owner,
  onSelectFile,
  onSelectModule,
  onSelectCoAuthor,
  rightRail,
}: OwnerProfileViewProps) {
  const tenureDays = useMemo(() => {
    if (!owner.first_commit_at) return null;
    const start = new Date(owner.first_commit_at).getTime();
    if (Number.isNaN(start)) return null;
    return Math.max(0, Math.floor((Date.now() - start) / 86_400_000));
  }, [owner.first_commit_at]);

  const categories = useMemo(
    () =>
      Object.entries(owner.commit_categories || {})
        .sort((a, b) => b[1] - a[1])
        .slice(0, 6),
    [owner.commit_categories],
  );
  const categoryTotal = categories.reduce((s, [, n]) => s + n, 0) || 1;

  return (
    <div className="space-y-6">
      {/* ---------- Header ---------- */}
      <Card>
        <CardContent className="p-5">
          <div className="flex flex-wrap items-start gap-5">
            <OwnerAvatar name={owner.name} email={owner.email} size="lg" />
            <div className="flex-1 min-w-0">
              <h1 className="flex flex-wrap items-center gap-2 text-2xl font-bold text-[var(--color-text-primary)]">
                {owner.name}
                {tenureDays !== null && tenureDays < 90 && (
                  <Badge variant="outline" className="text-[10px] font-medium">
                    new to this repo
                  </Badge>
                )}
              </h1>
              {owner.email && (
                <a
                  href={`mailto:${owner.email}`}
                  className="text-sm text-[var(--color-text-secondary)] hover:underline"
                >
                  {owner.email}
                </a>
              )}
              <p className="mt-2 text-xs text-[var(--color-text-tertiary)]">
                <Calendar className="inline h-3 w-3 mr-1" />
                {tenureDays !== null
                  ? `${tenureDays.toLocaleString()} days on this repo`
                  : "tenure unknown"}{" "}
                · first commit {fmtDate(owner.first_commit_at)} · last touched{" "}
                {timeAgo(owner.last_commit_at)}
              </p>
            </div>
          </div>

          <div className="mt-5 grid grid-cols-2 gap-3 sm:grid-cols-4 lg:grid-cols-6">
            <Headline label="Files owned" value={owner.files_owned} />
            <Headline label="Modules" value={owner.modules.length} />
            <Headline
              label="Commits / 90d"
              value={owner.commit_count_90d}
              icon={<GitCommit className="h-3.5 w-3.5" />}
            />
            <Headline
              label="Lines added (est)"
              value={fmtCompact(owner.lines_added_90d_est)}
              tone="add"
            />
            <Headline
              label="Lines deleted (est)"
              value={fmtCompact(owner.lines_deleted_90d_est)}
              tone="del"
            />
            <Headline label="Co-authors" value={owner.co_authors.length} />
          </div>
        </CardContent>
      </Card>

      {/* ---------- Risk strip ---------- */}
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <RiskTile
          icon={<Users className="h-4 w-4 text-[var(--color-warning)]" />}
          label="Silo modules"
          value={owner.silo_modules}
          help="modules where this person owns >80% of files"
          tone={owner.silo_modules > 0 ? "warn" : "ok"}
        />
        <RiskTile
          icon={<ShieldAlert className="h-4 w-4 text-[var(--color-error)]" />}
          label="Bus-factor risk"
          value={owner.bus_factor_risk_files}
          help="files with bus_factor ≤ 1 that they own"
          tone={owner.bus_factor_risk_files > 0 ? "danger" : "ok"}
        />
        <RiskTile
          icon={<Flame className="h-4 w-4 text-[var(--color-warning)]" />}
          label="Hotspots owned"
          value={owner.hotspots_owned}
          help="high-churn files where they are primary owner"
          tone={owner.hotspots_owned > 0 ? "warn" : "ok"}
        />
        <RiskTile
          icon={<Trash2 className="h-4 w-4 text-[var(--color-text-tertiary)]" />}
          label="Dead-code burden"
          value={`${owner.dead_code_files_owned} files · ${fmtCompact(owner.dead_code_lines_owned)} lines`}
          help="dead code findings whose primary owner is this person"
          tone={owner.dead_code_files_owned > 0 ? "muted" : "ok"}
        />
      </div>

      {/* ---------- Body ---------- */}
      <div className="grid gap-4 lg:grid-cols-3">
        <div className="space-y-4 lg:col-span-2">
          <Card>
            <CardHeader className="pb-2">
              <CardTitle className="text-sm flex items-center gap-1.5">
                <Folder className="h-4 w-4" /> Modules
              </CardTitle>
              <p className="text-xs text-[var(--color-text-tertiary)]">
                Where this person spends their time. Bars show their share of the module.
              </p>
            </CardHeader>
            <CardContent className="pt-0 space-y-1.5">
              {owner.modules.slice(0, 12).map((m) => (
                <ModuleRow key={m.module_path} mod={m} onClick={() => onSelectModule?.(m.module_path)} />
              ))}
              {owner.modules.length > 12 && (
                <p className="px-2 pt-1 text-[10px] text-[var(--color-text-tertiary)]">
                  +{owner.modules.length - 12} more modules not shown
                </p>
              )}
              {owner.modules.length === 0 && (
                <EmptyState
                  className="p-6"
                  title="No module attribution yet"
                  description="Module ownership appears after the next git sync."
                />
              )}
            </CardContent>
          </Card>

          <Card>
            <CardHeader className="pb-2">
              <CardTitle className="text-sm flex items-center gap-1.5">
                <TrendingUp className="h-4 w-4" /> Top files
              </CardTitle>
              <p className="text-xs text-[var(--color-text-tertiary)]">
                Files this person touches most, ordered by attributed commit count.
              </p>
            </CardHeader>
            <CardContent className="pt-0">
              <FileTable files={owner.top_files} onSelectFile={onSelectFile} />
              {(owner.files_touched_total ?? 0) > Math.min(owner.top_files.length, 20) && (
                <p className="pt-2 text-[10px] text-[var(--color-text-tertiary)]">
                  +{(owner.files_touched_total ?? 0) - Math.min(owner.top_files.length, 20)}{" "}
                  more files touched, not shown
                </p>
              )}
            </CardContent>
          </Card>
        </div>

        <div className="space-y-4">
          <Card>
            <CardHeader className="pb-2">
              <CardTitle className="text-sm flex items-center gap-1.5">
                <Users className="h-4 w-4" /> Co-authors
              </CardTitle>
              <p className="text-xs text-[var(--color-text-tertiary)]">
                People who edit the same files. Strong overlap = natural reviewer.
              </p>
              {owner.co_authors.filter((c) => c.co_change_strength >= 0.3).length > 0 && (
                <p className="text-[10px] text-[var(--color-text-secondary)]">
                  Would review well for this person&apos;s changes:{" "}
                  {owner.co_authors
                    .filter((c) => c.co_change_strength >= 0.3)
                    .slice(0, 2)
                    .map((c) => c.name)
                    .join(", ")}
                </p>
              )}
            </CardHeader>
            <CardContent className="pt-0 space-y-1.5">
              {owner.co_authors.slice(0, 10).map((c) => (
                <button
                  key={c.email ?? c.name}
                  onClick={() => onSelectCoAuthor?.(c)}
                  className="flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left text-xs hover:bg-[var(--color-bg-elevated)]"
                >
                  <OwnerAvatar name={c.name} email={c.email} size="sm" />
                  <div className="min-w-0 flex-1">
                    <div className="truncate font-medium text-[var(--color-text-primary)]">
                      {c.name}
                    </div>
                    <div className="text-[10px] text-[var(--color-text-tertiary)]">
                      {c.shared_files} shared {c.shared_files === 1 ? "file" : "files"} ·{" "}
                      {(c.co_change_strength * 100).toFixed(0)}% overlap
                    </div>
                  </div>
                  <div className="h-1 w-12 overflow-hidden rounded-full bg-[var(--color-bg-inset)]">
                    <div
                      className="h-full bg-[var(--color-accent-primary)]"
                      style={{ width: `${Math.min(100, c.co_change_strength * 100)}%` }}
                    />
                  </div>
                </button>
              ))}
              {(owner.co_authors_total ?? owner.co_authors.length) >
                Math.min(owner.co_authors.length, 10) && (
                <p className="px-2 pt-1 text-[10px] text-[var(--color-text-tertiary)]">
                  +
                  {(owner.co_authors_total ?? owner.co_authors.length) -
                    Math.min(owner.co_authors.length, 10)}{" "}
                  more co-authors not shown
                </p>
              )}
              {owner.co_authors.length === 0 && (
                <EmptyState
                  className="p-6"
                  title="No co-authors detected"
                  description="Nobody else edits the files this person owns."
                />
              )}
            </CardContent>
          </Card>

          {owner.agent_collab && owner.agent_collab.agent_commit_count > 0 && (
            <Card>
              <CardHeader className="pb-2">
                <CardTitle className="text-sm flex items-center gap-1.5">
                  <Bot className="h-4 w-4" /> Agent collaboration
                </CardTitle>
                <p className="text-xs text-[var(--color-text-tertiary)]">
                  Coding-agent activity on the files this person owns.
                </p>
              </CardHeader>
              <CardContent className="pt-0 space-y-2 text-xs">
                <div className="flex items-center justify-between">
                  <span className="text-[var(--color-text-secondary)]">
                    Agent-attributed commits
                  </span>
                  <span className="tabular-nums font-medium text-[var(--color-text-primary)]">
                    {owner.agent_collab.agent_commit_count}
                    {owner.agent_collab.agent_share_pct != null &&
                      ` (${Math.round(owner.agent_collab.agent_share_pct)}%)`}
                  </span>
                </div>
                <div className="flex items-center justify-between">
                  <span className="text-[var(--color-text-secondary)]">
                    Owned files with agent commits
                  </span>
                  <span className="tabular-nums font-medium text-[var(--color-text-primary)]">
                    {owner.agent_collab.files_with_agent_commits}
                  </span>
                </div>
                {Object.keys(owner.agent_collab.tier_counts).length > 0 && (
                  <div className="pt-1">
                    <AgentTierBar tierCounts={owner.agent_collab.tier_counts} />
                  </div>
                )}
              </CardContent>
            </Card>
          )}

          <Card>
            <CardHeader className="pb-2">
              <CardTitle className="text-sm">Commit mix</CardTitle>
              <p className="text-xs text-[var(--color-text-tertiary)]">
                Classification of commits across files this person touches.
              </p>
            </CardHeader>
            <CardContent className="pt-0">
              {categories.length === 0 ? (
                <EmptyState
                  className="p-6"
                  title="No category data"
                  description="Commit classification runs during indexing."
                />
              ) : (
                <div className="space-y-2">
                  {categories.map(([cat, n]) => (
                    <div key={cat}>
                      <div className="flex items-center justify-between text-xs">
                        <span className="capitalize text-[var(--color-text-secondary)]">
                          {cat}
                        </span>
                        <span className="tabular-nums text-[var(--color-text-tertiary)]">
                          {n}
                        </span>
                      </div>
                      <div className="mt-1 h-1 overflow-hidden rounded-full bg-[var(--color-bg-inset)]">
                        <div
                          className={cn("h-full", categoryColor(cat))}
                          style={{ width: `${(n / categoryTotal) * 100}%` }}
                        />
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </CardContent>
          </Card>

          {rightRail}
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Building blocks
// ---------------------------------------------------------------------------

function Headline({
  label,
  value,
  tone,
  icon,
}: {
  label: string;
  value: string | number;
  tone?: "add" | "del";
  icon?: React.ReactNode;
}) {
  const color =
    tone === "add"
      ? "text-[var(--color-success)]"
      : tone === "del"
        ? "text-[var(--color-error)]"
        : "text-[var(--color-text-primary)]";
  return (
    <div className="rounded-lg border border-[var(--color-border-default)] bg-[var(--color-bg-elevated)] px-3 py-2">
      <div className="flex items-center gap-1 text-[10px] uppercase tracking-wider text-[var(--color-text-tertiary)]">
        {icon}
        {label}
      </div>
      <div className={`mt-1 text-xl font-bold tabular-nums ${color}`}>{value}</div>
    </div>
  );
}

function RiskTile({
  icon,
  label,
  value,
  help,
  tone,
}: {
  icon: React.ReactNode;
  label: string;
  value: number | string;
  help: string;
  tone: "ok" | "warn" | "danger" | "muted";
}) {
  const border =
    tone === "danger"
      ? "border-[var(--color-error)]/40 bg-[var(--color-error)]/5"
      : tone === "warn"
        ? "border-[var(--color-warning)]/40 bg-[var(--color-warning)]/5"
        : tone === "muted"
          ? "border-[var(--color-border-default)] bg-[var(--color-bg-elevated)]"
          : "border-[var(--color-border-default)] bg-[var(--color-bg-elevated)]";
  return (
    <div className={cn("rounded-lg border p-3", border)}>
      <div className="flex items-center gap-1.5 text-xs uppercase tracking-wider text-[var(--color-text-tertiary)]">
        {icon}
        {label}
      </div>
      <div className="mt-1.5 text-lg font-semibold text-[var(--color-text-primary)]">
        {value}
      </div>
      <p className="mt-1 text-[10px] text-[var(--color-text-tertiary)]">{help}</p>
    </div>
  );
}

function ModuleRow({
  mod,
  onClick,
}: {
  mod: OwnerModuleRollup;
  onClick: () => void;
}) {
  const share = Math.min(100, Math.max(0, mod.dominant_pct * 100));
  return (
    <button
      onClick={onClick}
      className="group flex w-full items-center gap-3 rounded-md px-2 py-1.5 hover:bg-[var(--color-bg-elevated)]"
    >
      <span className="flex-1 truncate text-left text-xs font-medium text-[var(--color-text-primary)]">
        {mod.module_path}
      </span>
      {mod.hotspot_count > 0 && (
        <Badge variant="outdated" className="text-[10px]">
          <Flame className="mr-0.5 h-3 w-3" />
          {mod.hotspot_count}
        </Badge>
      )}
      <span className="w-12 text-right text-[10px] tabular-nums text-[var(--color-text-tertiary)]">
        {mod.file_count} files
      </span>
      <div className="h-1.5 w-24 overflow-hidden rounded-full bg-[var(--color-bg-inset)]">
        <div
          className={cn(
            "h-full",
            share > 80 ? "bg-[var(--color-warning)]" : "bg-[var(--color-accent-primary)]",
          )}
          style={{ width: `${share}%` }}
        />
      </div>
      <span className="w-10 text-right text-[10px] tabular-nums text-[var(--color-text-tertiary)]">
        {share.toFixed(0)}%
      </span>
    </button>
  );
}

const FILE_COLUMNS: ResponsiveColumn<OwnerFileEntry>[] = [
  {
    key: "file_path",
    header: "File",
    priority: 1,
    cellClassName: "max-w-[320px]",
    render: (f) => (
      <span className="flex items-center gap-1.5 truncate font-mono text-xs text-[var(--color-text-primary)]">
        {f.is_hotspot && <Flame className="h-3 w-3 shrink-0 text-[var(--color-warning)]" />}
        {truncatePath(f.file_path, 48)}
      </span>
    ),
  },
  {
    key: "commit_count_90d",
    header: "Commits / 90d",
    mobileLabel: "Commits",
    align: "right",
    priority: 2,
    cellClassName: "tabular-nums",
    render: (f) => f.commit_count_90d,
  },
  {
    key: "churn",
    header: "Churn",
    align: "right",
    priority: 2,
    cellClassName: "tabular-nums",
    render: (f) => <ChurnPill value={f.churn_percentile} />,
  },
  {
    key: "bus",
    header: "Bus",
    align: "right",
    priority: 3,
    cellClassName: "tabular-nums",
    render: (f) => <BusBadge bf={f.bus_factor} />,
  },
  {
    key: "touched",
    header: "Touched",
    align: "right",
    priority: 3,
    cellClassName: "text-[10px] text-[var(--color-text-tertiary)]",
    render: (f) => timeAgo(f.last_commit_at),
  },
];

function FileTable({
  files,
  onSelectFile,
}: {
  files: OwnerFileEntry[];
  onSelectFile?: ((path: string) => void) | undefined;
}) {
  return (
    <ResponsiveTable
      columns={FILE_COLUMNS}
      rows={files.slice(0, 20)}
      rowKey={(f) => f.file_path}
      onRowClick={onSelectFile ? (f) => onSelectFile(f.file_path) : undefined}
      bare
      empty={
        <EmptyState
          className="p-6"
          title="No file attribution"
          description="File-level ownership appears after the next git sync."
        />
      }
    />
  );
}

function ChurnPill({ value }: { value: number }) {
  const v = Math.round(value);
  const color =
    v >= 80
      ? "bg-[var(--color-error)]/20 text-[var(--color-error)]"
      : v >= 50
        ? "bg-[var(--color-warning)]/20 text-[var(--color-warning)]"
        : "bg-[var(--color-bg-inset)] text-[var(--color-text-tertiary)]";
  return (
    <span className={cn("inline-block rounded px-1.5 py-0.5 text-[10px] tabular-nums", color)}>
      {v}
    </span>
  );
}

function BusBadge({ bf }: { bf: number }) {
  const color =
    bf <= 1
      ? "text-[var(--color-error)]"
      : bf === 2
        ? "text-[var(--color-warning)]"
        : "text-[var(--color-success)]";
  return <span className={cn("font-semibold", color)}>{bf}</span>;
}

function categoryColor(category: string): string {
  const map: Record<string, string> = {
    feat: "bg-[var(--color-success)]",
    fix: "bg-[var(--color-error)]",
    refactor: "bg-[var(--color-accent-secondary)]",
    docs: "bg-[var(--color-info)]",
    test: "bg-[var(--color-accent-primary)]",
    chore: "bg-[var(--color-text-tertiary)]",
    perf: "bg-[var(--color-warning)]",
  };
  return map[category] ?? "bg-[var(--color-accent-primary)]";
}
