import { ArrowDownLeft, ArrowUpRight, Flame } from "lucide-react";
import { Badge } from "../ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "../ui/card";
import { scoreBadgeClass } from "../health/tokens";
import { fileEntityPath, symbolEntityPath } from "../shared/entity/routes";
import { truncatePath } from "../lib/format";
import type { SymbolCallEntry, SymbolDetailResponse } from "@repowise-dev/types/symbols";

export interface SymbolPageProps {
  data: SymbolDetailResponse;
  repoId: string;
  linkPrefix?: string;
}

function ageDays(medianAuthorTime: number | null | undefined): number | null {
  if (!medianAuthorTime) return null;
  return Math.max(0, Math.round((Date.now() / 1000 - medianAuthorTime) / 86400));
}

function CallList({
  title,
  icon,
  entries,
  symbolHref,
}: {
  title: string;
  icon: React.ReactNode;
  entries: SymbolCallEntry[];
  symbolHref: (id: string) => string;
}) {
  return (
    <Card>
      <CardHeader className="pb-2">
        <CardTitle className="text-sm font-medium flex items-center gap-2">
          {icon}
          {title}
          <span className="text-[10px] font-normal text-[var(--color-text-tertiary)] tabular-nums">
            {entries.length}
          </span>
        </CardTitle>
      </CardHeader>
      <CardContent>
        {entries.length === 0 ? (
          <p className="text-xs text-[var(--color-text-tertiary)]">None in the indexed graph.</p>
        ) : (
          <ul className="space-y-1">
            {entries.map((e) => (
              <li key={`${e.symbol_id}-${e.edge_type}`}>
                <a
                  href={symbolHref(e.symbol_id)}
                  className="flex items-center gap-2 -mx-2 px-2 py-0.5 rounded hover:bg-[var(--color-bg-elevated)] transition-colors"
                >
                  <span className="text-[11px] font-mono text-[var(--color-text-primary)] truncate">
                    {e.name}
                  </span>
                  <span className="text-[10px] text-[var(--color-text-tertiary)] truncate flex-1 min-w-0">
                    {truncatePath(e.file, 36)}
                  </span>
                  <span className="text-[10px] text-[var(--color-text-tertiary)] shrink-0">
                    {e.edge_type}
                  </span>
                </a>
              </li>
            ))}
          </ul>
        )}
      </CardContent>
    </Card>
  );
}

/**
 * The canonical symbol page: signature + docstring, function blame (churn,
 * age, owner), callers/callees linking to sibling symbol pages, governing
 * decisions, and the parent-file context. Purely presentational.
 */
export function SymbolPage({ data, repoId, linkPrefix }: SymbolPageProps) {
  const prefix = linkPrefix ?? `/repos/${repoId}`;
  const s = data.symbol;
  const symbolHref = (id: string) => symbolEntityPath(prefix, id);
  const fileHref = fileEntityPath(prefix, data.file_context.file_path);
  const age = ageDays(s.blame_median_author_time);

  return (
    <div className="space-y-4">
      {/* ── Header ── */}
      <div className="space-y-2">
        <div className="flex flex-wrap items-baseline gap-x-2 gap-y-1">
          <h1 className="font-mono text-lg text-[var(--color-text-primary)] break-all">
            {s.name}
          </h1>
          <Badge variant="outline" className="text-[10px] h-5">
            {s.kind}
          </Badge>
          {s.is_async && (
            <Badge variant="outline" className="text-[10px] h-5">
              async
            </Badge>
          )}
          <Badge variant="outline" className="text-[10px] h-5 capitalize">
            {s.visibility}
          </Badge>
          {s.file_is_hotspot && (
            <Badge variant="outline" className="text-[10px] h-5 text-[var(--color-error)] border-[var(--color-error)]/30">
              <Flame className="h-2.5 w-2.5" /> hot file
            </Badge>
          )}
        </div>
        <a
          href={fileHref}
          className="inline-block text-xs font-mono text-[var(--color-text-secondary)] hover:text-[var(--color-accent-primary)] hover:underline break-all"
        >
          {s.file_path}:{s.start_line}
        </a>
        {data.governing_decisions.length > 0 && (
          <div className="flex flex-wrap items-center gap-1.5">
            <span className="text-[10px] uppercase tracking-wider text-[var(--color-text-tertiary)]">
              Governed by
            </span>
            {data.governing_decisions.map((d) => (
              <a
                key={d.id}
                href={`${prefix}/decisions/${d.id}`}
                className="rounded border border-[var(--color-border-default)] px-1.5 py-0.5 text-[11px] text-[var(--color-text-secondary)] hover:text-[var(--color-accent-primary)] hover:border-[var(--color-accent-primary)] transition-colors"
              >
                {d.title}
              </a>
            ))}
          </div>
        )}
      </div>

      {/* ── Signature + docstring ── */}
      {s.signature && (
        <pre className="overflow-x-auto rounded-md border border-[var(--color-border-default)] bg-[var(--color-bg-elevated)] p-3 text-xs font-mono text-[var(--color-text-primary)]">
          {s.signature}
        </pre>
      )}
      {s.docstring && (
        <p className="text-xs text-[var(--color-text-secondary)] whitespace-pre-wrap leading-relaxed">
          {s.docstring}
        </p>
      )}

      {/* ── Signals strip ── */}
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4 lg:grid-cols-6">
        <Stat
          label="Importance"
          value={s.importance_score != null ? s.importance_score.toFixed(3) : "—"}
        />
        <Stat label="Complexity" value={String(s.complexity_estimate)} />
        <Stat label="Callers" value={String(data.graph.in_degree)} />
        <Stat label="Callees" value={String(data.graph.out_degree)} />
        <Stat
          label="Modifications"
          value={s.blame_mod_count != null ? String(s.blame_mod_count) : "—"}
          hint={
            s.blame_recent_mod_count != null
              ? `${s.blame_recent_mod_count} recent`
              : undefined
          }
        />
        <Stat label="Median age" value={age != null ? `${age}d` : "—"} />
      </div>

      {(s.blame_owner_name || data.file_context.primary_owner) && (
        <p className="text-xs text-[var(--color-text-secondary)]">
          {s.blame_owner_name ? (
            <>
              Blame owner{" "}
              <a
                href={`${prefix}/owners/${encodeURIComponent(s.blame_owner_name)}`}
                className="font-medium hover:text-[var(--color-accent-primary)] hover:underline"
              >
                {s.blame_owner_name}
              </a>
              {s.blame_owner_line_pct != null &&
                ` (${Math.round(s.blame_owner_line_pct * 100)}% of lines)`}
            </>
          ) : (
            <>
              File owner{" "}
              <a
                href={`${prefix}/owners/${encodeURIComponent(data.file_context.primary_owner!)}`}
                className="font-medium hover:text-[var(--color-accent-primary)] hover:underline"
              >
                {data.file_context.primary_owner}
              </a>
            </>
          )}
        </p>
      )}

      {/* ── Callers / callees ── */}
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        <CallList
          title="Callers"
          icon={<ArrowDownLeft className="h-4 w-4 text-[var(--color-text-secondary)]" />}
          entries={data.graph.callers}
          symbolHref={symbolHref}
        />
        <CallList
          title="Callees"
          icon={<ArrowUpRight className="h-4 w-4 text-[var(--color-text-secondary)]" />}
          entries={data.graph.callees}
          symbolHref={symbolHref}
        />
      </div>

      {/* ── Parent file context ── */}
      <Card>
        <CardHeader className="pb-2">
          <CardTitle className="text-sm font-medium">Parent file</CardTitle>
        </CardHeader>
        <CardContent className="flex flex-wrap items-center gap-3">
          <a
            href={fileHref}
            className="text-xs font-mono text-[var(--color-accent-primary)] hover:underline break-all"
          >
            {data.file_context.file_path}
          </a>
          {data.file_context.health_score != null && (
            <span
              className={`inline-flex items-baseline rounded px-1.5 py-0.5 text-xs font-bold tabular-nums ${scoreBadgeClass(data.file_context.health_score)}`}
            >
              {data.file_context.health_score.toFixed(1)}
              <span className="ml-0.5 text-[9px] font-normal opacity-70">/10</span>
            </span>
          )}
          {data.file_context.language && (
            <Badge variant="outline" className="text-[10px] h-5 capitalize">
              {data.file_context.language}
            </Badge>
          )}
        </CardContent>
      </Card>
    </div>
  );
}

function Stat({
  label,
  value,
  hint,
}: {
  label: string;
  value: string;
  hint?: string | undefined;
}) {
  return (
    <div className="rounded-md border border-[var(--color-border-default)] bg-[var(--color-bg-elevated)] p-2.5">
      <p className="text-[10px] uppercase tracking-wider text-[var(--color-text-tertiary)] mb-0.5">
        {label}
      </p>
      <p className="text-sm font-semibold tabular-nums text-[var(--color-text-primary)]">{value}</p>
      {hint && <p className="text-[10px] text-[var(--color-text-tertiary)]">{hint}</p>}
    </div>
  );
}
