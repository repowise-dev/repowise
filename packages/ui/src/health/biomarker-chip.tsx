"use client";

import { Info } from "lucide-react";
import { biomarkerInfo, CATEGORY_LABEL } from "./biomarker-glossary";

export interface BiomarkerChipProps {
  type: string;
  showInfo?: boolean;
  className?: string;
}

export function BiomarkerChip({ type, showInfo = true, className = "" }: BiomarkerChipProps) {
  const info = biomarkerInfo(type);
  const title = `${info.label} · ${CATEGORY_LABEL[info.category]}\n\n${info.description}`;
  return (
    <span
      className={`inline-flex items-center gap-1 text-xs font-medium text-[var(--color-text-primary)] ${className}`}
      title={title}
    >
      {info.label}
      {showInfo && info.description ? (
        <Info className="h-3 w-3 text-[var(--color-text-tertiary)]" aria-hidden />
      ) : null}
    </span>
  );
}
