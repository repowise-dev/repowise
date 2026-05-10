"use client";

import {
  FileText,
  GitBranch,
  BookOpen,
  Radius,
  Code2,
  Trash2,
  ShieldAlert,
  Flame,
  Users,
  ExternalLink,
} from "lucide-react";
import { Badge } from "../../ui/badge";
import { formatLOC } from "../../lib/format";
import { cn } from "../../lib/cn";
import type { FileCardData, FileCardLinks } from "./types";

export interface FileCardProps {
  data: FileCardData;
  links?: FileCardLinks | undefined;
  /** Hide the file path header (when used inside a Dialog that already shows it). */
  hideHeader?: boolean;
  className?: string;
}

function Section({ title, icon: Icon, children }: { title: string; icon: React.ComponentType<{ className?: string }>; children: React.ReactNode }) {
  return (
    <div>
      <div className="flex items-center gap-1.5 mb-1.5">
        <Icon className="h-3.5 w-3.5 text-[var(--color-text-tertiary)]" />
        <span className="text-[10px] font-medium text-[var(--color-text-tertiary)] uppercase tracking-wider">
          {title}
        </span>
      </div>
      <div className="text-xs text-[var(--color-text-secondary)] space-y-1">{children}</div>
    </div>
  );
}

function Stat({ label, value, accent }: { label: string; value: React.ReactNode; accent?: string }) {
  return (
    <div className="flex items-baseline justify-between gap-2">
      <span className="text-[var(--color-text-tertiary)]">{label}</span>
      <span className={cn("tabular-nums font-medium", accent ?? "text-[var(--color-text-primary)]")}>{value}</span>
    </div>
  );
}

function LinkButton({ href, icon: Icon, label }: { href: string; icon: React.ComponentType<{ className?: string }>; label: string }) {
  return (
    <a
      href={href}
      className="inline-flex items-center gap-1 rounded-md border border-[var(--color-border-default)] bg-[var(--color-bg-elevated)] px-2 py-1 text-[11px] text-[var(--color-text-secondary)] hover:border-[var(--color-accent-primary)] hover:text-[var(--color-text-primary)] transition-colors"
    >
      <Icon className="h-3 w-3" />
      <span>{label}</span>
    </a>
  );
}

export function FileCard({ data, links, hideHeader = false, className }: FileCardProps) {
  const { git, docs, symbols, deadCode, decisions, security } = data;
  const busFactor = git?.bus_factor;
  const isHotspot = git?.is_hotspot;

  return (
    <div className={cn("space-y-4", className)}>
      {!hideHeader && (
        <div className="space-y-1">
          <div className="flex items-start gap-2">
            <FileText className="h-4 w-4 text-[var(--color-text-tertiary)] shrink-0 mt-0.5" />
            <p className="text-sm font-mono text-[var(--color-text-primary)] break-all leading-snug">
              {data.file_path}
            </p>
          </div>
          <div className="flex flex-wrap gap-1.5 pl-6">
            {data.language && <Badge variant="outline">{data.language}</Badge>}
            {isHotspot && (
              <Badge variant="outdated" className="gap-1">
                <Flame className="h-3 w-3" /> hotspot
              </Badge>
            )}
            {busFactor !== undefined && busFactor <= 1 && (
              <Badge variant="outline" className="gap-1 text-red-400 border-red-400/30">
                <Users className="h-3 w-3" /> bus×{busFactor}
              </Badge>
            )}
          </div>
          {data.summary && (
            <p className="pl-6 text-xs text-[var(--color-text-secondary)] leading-relaxed">
              {data.summary}
            </p>
          )}
        </div>
      )}

      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        {git && (
          <Section title="Git activity (90d)" icon={GitBranch}>
            {git.commit_count_90d !== undefined && (
              <Stat label="Commits" value={git.commit_count_90d} />
            )}
            {git.churn_percentile !== undefined && (
              <Stat label="Churn percentile" value={`${Math.round(git.churn_percentile)}%`} />
            )}
            {(git.lines_added_90d !== undefined || git.lines_deleted_90d !== undefined) && (
              <Stat
                label="Lines"
                value={
                  <>
                    <span className="text-green-400">+{formatLOC(git.lines_added_90d ?? 0)}</span>{" "}
                    <span className="text-red-400">-{formatLOC(git.lines_deleted_90d ?? 0)}</span>
                  </>
                }
              />
            )}
            {git.primary_owner && (
              <Stat label="Owner" value={<span className="truncate max-w-[160px] inline-block align-bottom">{git.primary_owner}</span>} />
            )}
            {git.temporal_hotspot_score != null && (
              <Stat
                label="Trend score"
                value={git.temporal_hotspot_score.toFixed(2)}
                accent={
                  git.temporal_hotspot_score >= 5
                    ? "text-red-400"
                    : git.temporal_hotspot_score >= 2
                      ? "text-orange-400"
                      : "text-[var(--color-text-secondary)]"
                }
              />
            )}
          </Section>
        )}

        {docs && (
          <Section title="Documentation" icon={BookOpen}>
            {docs.has_doc ? (
              <>
                <Stat
                  label="Status"
                  value={<span className="text-green-400">Indexed</span>}
                />
                {docs.freshness_pct !== undefined && (
                  <Stat label="Freshness" value={`${Math.round(docs.freshness_pct)}%`} />
                )}
                {docs.last_updated && (
                  <Stat label="Updated" value={docs.last_updated} />
                )}
                {docs.doc_url && (
                  <a
                    href={docs.doc_url}
                    className="inline-flex items-center gap-1 mt-1 text-[var(--color-accent-primary)] hover:underline text-[11px]"
                  >
                    Open doc <ExternalLink className="h-3 w-3" />
                  </a>
                )}
              </>
            ) : (
              <p className="text-[var(--color-text-tertiary)] italic text-[11px]">
                No doc generated for this file yet.
              </p>
            )}
          </Section>
        )}

        {symbols && (
          <Section title={`Symbols (${symbols.total})`} icon={Code2}>
            {symbols.top && symbols.top.length > 0 ? (
              symbols.top.slice(0, 5).map((s) => (
                <div key={s.id} className="flex items-baseline justify-between gap-2 text-[11px]">
                  <span className="font-mono text-[var(--color-text-primary)] truncate">{s.name}</span>
                  {s.kind && <span className="text-[var(--color-text-tertiary)] shrink-0">{s.kind}</span>}
                </div>
              ))
            ) : (
              <p className="text-[var(--color-text-tertiary)] italic text-[11px]">No symbols indexed.</p>
            )}
          </Section>
        )}

        {decisions && decisions.count > 0 && (
          <Section title={`Decisions (${decisions.count})`} icon={BookOpen}>
            {decisions.titles && decisions.titles.length > 0 ? (
              decisions.titles.slice(0, 4).map((t, i) => (
                <p key={i} className="text-[11px] text-[var(--color-text-primary)] truncate">
                  · {t}
                </p>
              ))
            ) : (
              <p className="text-[11px] text-[var(--color-text-tertiary)]">
                {decisions.count} decision{decisions.count === 1 ? "" : "s"} reference this file.
              </p>
            )}
          </Section>
        )}

        {deadCode && deadCode.findings_count > 0 && (
          <Section title="Dead code" icon={Trash2}>
            <Stat label="Findings" value={deadCode.findings_count} accent="text-red-400" />
            {deadCode.reclaimable_lines !== undefined && (
              <Stat
                label="Lines reclaimable"
                value={formatLOC(deadCode.reclaimable_lines)}
                accent="text-red-400"
              />
            )}
          </Section>
        )}

        {security && security.findings_count > 0 && (
          <Section title="Security" icon={ShieldAlert}>
            <Stat label="Findings" value={security.findings_count} accent="text-red-400" />
            {security.critical_count !== undefined && security.critical_count > 0 && (
              <Stat
                label="Critical"
                value={security.critical_count}
                accent="text-red-400"
              />
            )}
          </Section>
        )}
      </div>

      {links && Object.values(links).some(Boolean) && (
        <div className="flex flex-wrap gap-1.5 pt-2 border-t border-[var(--color-border-default)]">
          {links.graph && <LinkButton href={links.graph} icon={GitBranch} label="Graph" />}
          {links.docs && <LinkButton href={links.docs} icon={BookOpen} label="Docs" />}
          {links.symbols && <LinkButton href={links.symbols} icon={Code2} label="Symbols" />}
          {links.blastRadius && <LinkButton href={links.blastRadius} icon={Radius} label="Blast Radius" />}
          {links.decisions && <LinkButton href={links.decisions} icon={BookOpen} label="Decisions" />}
          {links.deadCode && <LinkButton href={links.deadCode} icon={Trash2} label="Dead code" />}
          {links.security && <LinkButton href={links.security} icon={ShieldAlert} label="Security" />}
        </div>
      )}
    </div>
  );
}
