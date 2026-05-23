"use client";

import { ArrowLeftRight } from "lucide-react";

export type BiomarkerDetailsRecord = Record<string, unknown>;

export interface BiomarkerDetailsProps {
  biomarkerType: string;
  details?: BiomarkerDetailsRecord | null | undefined;
  onPartnerSelect?: ((path: string) => void) | undefined;
  onPartnerHref?: ((path: string) => string) | undefined;
}

function num(v: unknown): number | null {
  if (typeof v === "number" && Number.isFinite(v)) return v;
  if (typeof v === "string" && v !== "" && Number.isFinite(Number(v))) {
    return Number(v);
  }
  return null;
}

function str(v: unknown): string | null {
  return typeof v === "string" && v.length > 0 ? v : null;
}

export function BiomarkerDetails({
  biomarkerType,
  details,
  onPartnerSelect,
  onPartnerHref,
}: BiomarkerDetailsProps) {
  if (!details) return null;

  if (biomarkerType === "hidden_coupling") {
    const partner = str(details.partner);
    const correlation = num(details.correlation);
    const coChanges = num(details.co_change_count);
    if (!partner) return null;
    const corrPct =
      correlation != null ? `${Math.round(correlation * 100)}%` : null;
    const href = onPartnerHref ? onPartnerHref(partner) : undefined;
    const inner = (
      <span className="inline-flex items-center gap-1 font-mono truncate">
        <ArrowLeftRight className="h-3 w-3 shrink-0" aria-hidden="true" />
        <span className="truncate">{partner}</span>
      </span>
    );
    return (
      <div className="text-[11px] text-[var(--color-text-tertiary)]">
        {href ? (
          <a
            href={href}
            onClick={(e) => {
              if (onPartnerSelect) {
                e.preventDefault();
                onPartnerSelect(partner);
              }
            }}
            className="text-[var(--color-accent-primary)] hover:underline"
          >
            {inner}
          </a>
        ) : onPartnerSelect ? (
          <button
            type="button"
            onClick={(e) => {
              e.stopPropagation();
              onPartnerSelect(partner);
            }}
            className="text-[var(--color-accent-primary)] hover:underline text-left"
          >
            {inner}
          </button>
        ) : (
          inner
        )}
        {corrPct || coChanges != null ? (
          <span className="ml-1 tabular-nums">
            {corrPct ? ` · ${corrPct} co-change` : ""}
            {coChanges != null ? ` · ${coChanges} commits` : ""}
          </span>
        ) : null}
      </div>
    );
  }

  if (biomarkerType === "complex_conditional") {
    const ops = num(details.operator_count);
    if (ops == null) return null;
    return (
      <div className="text-[11px] text-[var(--color-text-tertiary)] tabular-nums">
        ({ops} ops)
      </div>
    );
  }

  if (biomarkerType === "function_hotspot") {
    const mod =
      num(details.modification_count) ?? num(details.mod_count);
    const p80 = num(details.repo_p80) ?? num(details.p80);
    if (mod == null) return null;
    return (
      <div className="text-[11px] text-[var(--color-text-tertiary)] tabular-nums">
        mod {mod}
        {p80 != null ? ` / p80 ${p80}` : ""}
      </div>
    );
  }

  if (biomarkerType === "code_age_volatility") {
    const age = num(details.median_age_days);
    const recent = num(details.recent_mod_count);
    if (age == null && recent == null) return null;
    return (
      <div className="text-[11px] text-[var(--color-text-tertiary)] tabular-nums">
        {age != null ? `~${age}d old` : ""}
        {age != null && recent != null ? " · " : ""}
        {recent != null ? `${recent} edits/30d` : ""}
      </div>
    );
  }

  return null;
}
