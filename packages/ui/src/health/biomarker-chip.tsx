"use client";

import { Info } from "lucide-react";
import { Popover, PopoverContent, PopoverTrigger } from "../ui/popover";
import {
  biomarkerInfo,
  biomarkerDimension,
  CATEGORY_LABEL,
  DIMENSION_LABEL,
} from "./biomarker-glossary";

export interface BiomarkerChipProps {
  type: string;
  showInfo?: boolean;
  className?: string;
}

/**
 * A biomarker label with its glossary entry in a click/keyboard popover, so
 * the definition is reachable on touch and by keyboard, not just mouse hover.
 */
export function BiomarkerChip({ type, showInfo = true, className = "" }: BiomarkerChipProps) {
  const info = biomarkerInfo(type);
  const dimension = biomarkerDimension(type);

  if (!showInfo || !info.description) {
    return (
      <span
        className={`inline-flex items-center gap-1 text-xs font-medium text-[var(--color-text-primary)] ${className}`}
      >
        {info.label}
      </span>
    );
  }

  return (
    <Popover>
      <PopoverTrigger asChild>
        <button
          type="button"
          onClick={(e) => e.stopPropagation()}
          aria-label={`What is ${info.label}?`}
          className={`inline-flex items-center gap-1 text-xs font-medium text-[var(--color-text-primary)] rounded-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-accent-primary)] ${className}`}
        >
          {info.label}
          <Info className="h-3 w-3 text-[var(--color-text-tertiary)]" aria-hidden />
        </button>
      </PopoverTrigger>
      <PopoverContent
        align="start"
        onClick={(e) => e.stopPropagation()}
        className="space-y-1.5"
      >
        <p className="font-medium text-[var(--color-text-primary)]">{info.label}</p>
        <p className="text-[var(--color-text-tertiary)]">
          {CATEGORY_LABEL[info.category]} · {DIMENSION_LABEL[dimension]}
        </p>
        <p className="text-[var(--color-text-secondary)] leading-relaxed">{info.description}</p>
      </PopoverContent>
    </Popover>
  );
}
