"use client";

import { typeAccent } from "./meta";
import {
  cutEdges,
  cycleMembers,
  extractClassGroups,
  extractHelperOccurrences,
  moveTarget,
  splitGroups,
  splitResidual,
  type RefactoringPlan,
} from "./types";

export interface PlanBeforeProps {
  plan: RefactoringPlan;
}

function shortFile(path: string): string {
  const parts = path.split("/");
  return parts.length <= 2 ? path : `…/${parts.slice(-2).join("/")}`;
}

function baseName(path: string): string {
  const parts = path.split("/");
  return parts[parts.length - 1] ?? path;
}

/**
 * The "before" half of the before→after comparison: a synthesized picture of
 * the problem that triggered the plan. Deliberately shaped differently per type
 * so the issue reads at a glance — a tangled class, stacked duplicate sites, a
 * misplaced method reaching across, or a closed import loop.
 */
export function PlanBefore({ plan }: PlanBeforeProps) {
  const accent = typeAccent(plan.refactoring_type);

  if (plan.refactoring_type === "extract_class") {
    const groups = extractClassGroups(plan);
    const methods = groups.flatMap((g) => g.methods);
    const fields = groups.flatMap((g) => g.fields);
    const lcom4 = Number(plan.evidence?.lcom4 ?? 0);
    const shownM = methods.slice(0, 12);
    const shownF = fields.slice(0, 6);
    return (
      <div className="rounded-xl border border-[var(--color-border-default)] bg-[var(--color-bg-surface)] p-3.5">
        <div className="mb-2.5 flex items-center justify-between gap-2">
          <code className="truncate text-xs font-semibold text-[var(--color-text-primary)]">
            {plan.target_symbol || baseName(plan.file_path)}
          </code>
          {lcom4 > 0 ? (
            <span className="shrink-0 rounded-full bg-[var(--color-error)]/10 px-2 py-0.5 text-[10px] font-semibold tabular-nums text-[var(--color-error)]">
              LCOM4 {lcom4}
            </span>
          ) : null}
        </div>
        <p className="mb-2 text-[11px] text-[var(--color-text-tertiary)]">
          {methods.length} method{methods.length === 1 ? "" : "s"} and {fields.length} field
          {fields.length === 1 ? "" : "s"} packed into one class with no clear seams.
        </p>
        <div className="flex flex-wrap gap-1">
          {shownF.map((f) => (
            <code
              key={`f-${f}`}
              className="rounded bg-[var(--color-bg-elevated)] px-1.5 py-0.5 text-[11px] text-[var(--color-text-tertiary)]"
            >
              {f}
            </code>
          ))}
          {shownM.map((m) => (
            <code
              key={`m-${m}`}
              className="rounded bg-[var(--color-bg-elevated)] px-1.5 py-0.5 text-[11px] text-[var(--color-text-secondary)]"
            >
              {m}
            </code>
          ))}
          {methods.length + fields.length > shownM.length + shownF.length ? (
            <span className="px-1 py-0.5 text-[11px] text-[var(--color-text-tertiary)]">
              +{methods.length + fields.length - shownM.length - shownF.length} more
            </span>
          ) : null}
        </div>
      </div>
    );
  }

  if (plan.refactoring_type === "extract_helper") {
    const occ = extractHelperOccurrences(plan);
    const lines = Number(plan.evidence?.duplicated_lines ?? 0);
    const shown = occ.slice(0, 5);
    return (
      <div className="space-y-2">
        <p className="text-[11px] text-[var(--color-text-tertiary)]">
          The same {lines ? `~${lines}-line ` : ""}block is copy-pasted across {occ.length} site
          {occ.length === 1 ? "" : "s"}.
        </p>
        <div className="space-y-1.5">
          {shown.map((o, i) => (
            <div
              key={i}
              className="flex items-center justify-between gap-3 rounded-lg border border-dashed border-[var(--color-border-default)] bg-[var(--color-bg-surface)] px-3 py-2"
            >
              <code className="truncate text-[11px] text-[var(--color-text-secondary)]" title={o.file}>
                {shortFile(o.file)}
              </code>
              <span className="shrink-0 text-[10px] uppercase tracking-wide text-[var(--color-text-tertiary)]">
                duplicate
              </span>
            </div>
          ))}
          {occ.length > shown.length ? (
            <p className="px-1 text-[11px] text-[var(--color-text-tertiary)]">
              +{occ.length - shown.length} more copies
            </p>
          ) : null}
        </div>
      </div>
    );
  }

  if (plan.refactoring_type === "move_method") {
    const mv = moveTarget(plan);
    if (!mv) return null;
    const foreign = Number(plan.evidence?.foreign_calls ?? 0);
    const own = Number(plan.evidence?.own_calls ?? 0);
    return (
      <div className="rounded-xl border border-[var(--color-border-default)] bg-[var(--color-bg-surface)] p-3.5">
        <div className="rounded-lg border border-[var(--color-border-default)] bg-[var(--color-bg-elevated)] p-2.5">
          <div className="text-[10px] uppercase tracking-wide text-[var(--color-text-tertiary)]">
            Currently in
          </div>
          <code className="text-xs text-[var(--color-text-secondary)]">{mv.from_class}</code>
          <div className="mt-1.5 inline-flex items-center gap-1 rounded-md border border-dashed px-1.5 py-0.5" style={{ borderColor: accent }}>
            <code className="text-[11px] text-[var(--color-text-primary)]">{mv.method}()</code>
          </div>
        </div>
        <p className="mt-2.5 text-[11px] leading-relaxed text-[var(--color-text-tertiary)]">
          It reaches into{" "}
          <code className="text-[var(--color-text-secondary)]">{mv.to_class}</code>
          {foreign || own ? (
            <>
              {" "}
              {foreign} time{foreign === 1 ? "" : "s"} vs {own} call{own === 1 ? "" : "s"} to its own
              class — it belongs elsewhere.
            </>
          ) : (
            " more than its own class — it belongs elsewhere."
          )}
        </p>
      </div>
    );
  }

  if (plan.refactoring_type === "break_cycle") {
    return <CycleGraph plan={plan} accent={accent} />;
  }

  if (plan.refactoring_type === "split_file") {
    const symbols = [...splitGroups(plan).flatMap((g) => g.symbols), ...splitResidual(plan)];
    const nloc = Number(plan.evidence?.file_nloc ?? 0);
    const count = Number(plan.evidence?.symbol_count ?? symbols.length);
    const shown = symbols.slice(0, 18);
    return (
      <div className="rounded-xl border border-[var(--color-border-default)] bg-[var(--color-bg-surface)] p-3.5">
        <div className="mb-2.5 flex items-center justify-between gap-2">
          <code className="truncate text-xs font-semibold text-[var(--color-text-primary)]">
            {baseName(plan.file_path)}
          </code>
          {nloc > 0 ? (
            <span className="shrink-0 rounded-full bg-[var(--color-error)]/10 px-2 py-0.5 text-[10px] font-semibold tabular-nums text-[var(--color-error)]">
              {nloc} NLOC
            </span>
          ) : null}
        </div>
        <p className="mb-2 text-[11px] text-[var(--color-text-tertiary)]">
          {count} top-level symbol{count === 1 ? "" : "s"} in one module — loosely related, no clear
          seams.
        </p>
        <div className="flex flex-wrap gap-1">
          {shown.map((s) => (
            <code
              key={s}
              className="rounded bg-[var(--color-bg-elevated)] px-1.5 py-0.5 text-[11px] text-[var(--color-text-tertiary)]"
            >
              {s}
            </code>
          ))}
          {symbols.length > shown.length ? (
            <span className="px-1 py-0.5 text-[11px] text-[var(--color-text-tertiary)]">
              +{symbols.length - shown.length} more
            </span>
          ) : null}
        </div>
      </div>
    );
  }

  return null;
}

/** A small ring of the cycle members with arrows closing the loop. */
function CycleGraph({ plan, accent }: { plan: RefactoringPlan; accent: string }) {
  const members = cycleMembers(plan);
  const edges = cutEdges(plan);
  const n = Math.min(members.length, 8);
  if (n < 2) {
    return (
      <div className="rounded-xl border border-[var(--color-border-default)] bg-[var(--color-bg-surface)] p-3.5 text-[11px] text-[var(--color-text-tertiary)]">
        {members.length} files form an import cycle.
      </div>
    );
  }
  // Wider canvas leaves room for a file label outside each node.
  const W = 360;
  const H = 210;
  const cx = W / 2;
  const cy = H / 2;
  const r = 60;
  const cut = new Set(edges.map((e) => `${e.from}→${e.to}`));
  const pts = Array.from({ length: n }, (_, i) => {
    const a = (i / n) * Math.PI * 2 - Math.PI / 2;
    const cos = Math.cos(a);
    const sin = Math.sin(a);
    return {
      x: cx + r * cos,
      y: cy + r * sin,
      lx: cx + (r + 11) * cos,
      ly: cy + (r + 11) * sin,
      anchor: (cos > 0.15 ? "start" : cos < -0.15 ? "end" : "middle") as
        | "start"
        | "end"
        | "middle",
      file: members[i] ?? "",
    };
  });

  return (
    <div className="rounded-xl border border-[var(--color-border-default)] bg-[var(--color-bg-surface)] p-2">
      <svg viewBox={`0 0 ${W} ${H}`} width="100%" height={H} role="img" aria-label="Import cycle">
        {pts.map((p, i) => {
          const q = pts[(i + 1) % n]!;
          const isCut = cut.has(`${p.file}→${q.file}`) || cut.has(`${q.file}→${p.file}`);
          return (
            <line
              key={`e-${i}`}
              x1={p.x}
              y1={p.y}
              x2={q.x}
              y2={q.y}
              stroke={isCut ? "var(--color-error)" : accent}
              strokeWidth={isCut ? 2.5 : 1.6}
              strokeDasharray={isCut ? "4 4" : undefined}
              strokeOpacity={isCut ? 0.9 : 0.5}
            />
          );
        })}
        {pts.map((p, i) => (
          <g key={`n-${i}`}>
            <circle cx={p.x} cy={p.y} r={9} fill={accent} fillOpacity={0.9} />
            <text
              x={p.x}
              y={p.y}
              textAnchor="middle"
              dominantBaseline="central"
              fontSize={9}
              fontWeight={600}
              fill="var(--color-bg-surface)"
            >
              {i + 1}
            </text>
            <text
              x={p.lx}
              y={p.ly}
              textAnchor={p.anchor}
              dominantBaseline="central"
              fontSize={10}
              fill="currentColor"
              className="text-[var(--color-text-secondary)]"
            >
              {baseName(p.file)}
            </text>
            <title>{p.file}</title>
          </g>
        ))}
      </svg>
      <p className="px-2 pb-1 text-center text-[11px] text-[var(--color-text-tertiary)]">
        {members.length} files import each other in a closed loop.
      </p>
    </div>
  );
}
