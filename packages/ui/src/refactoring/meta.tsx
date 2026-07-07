"use client";

import * as React from "react";
import { ArrowRightLeft, CopyMinus, FileStack, Scissors, Split, Unlink } from "lucide-react";
import type { Confidence, EffortBucket, RefactoringType } from "./types";

export interface RefactoringTypeMeta {
  label: string;
  /** One-line description of what the refactoring does. */
  blurb: string;
  Icon: React.ComponentType<{ className?: string; style?: React.CSSProperties }>;
  /** A CSS var name used for the type's accent (text + tinted backgrounds). */
  accentVar: string;
}

export const TYPE_META: Record<string, RefactoringTypeMeta> = {
  extract_class: {
    label: "Extract Class",
    blurb: "Split a low-cohesion class into focused, single-responsibility classes.",
    Icon: Split,
    accentVar: "--color-refactor-extract-class",
  },
  extract_helper: {
    label: "Extract Helper",
    blurb: "Dedupe a repeated block into one shared helper.",
    Icon: CopyMinus,
    accentVar: "--color-refactor-extract-helper",
  },
  extract_method: {
    label: "Extract Method",
    blurb: "Lift a cohesive slice of a long function into a focused helper.",
    Icon: Scissors,
    accentVar: "--color-refactor-extract-method",
  },
  move_method: {
    label: "Move Method",
    blurb: "Move a method to the class it actually belongs to.",
    Icon: ArrowRightLeft,
    accentVar: "--color-refactor-move-method",
  },
  break_cycle: {
    label: "Break Cycle",
    blurb: "Cut the minimal import edge that closes a dependency cycle.",
    Icon: Unlink,
    accentVar: "--color-refactor-break-cycle",
  },
  split_file: {
    label: "Split File",
    blurb: "Decompose an oversized module into cohesive files along its natural seams.",
    Icon: FileStack,
    accentVar: "--color-refactor-split-file",
  },
};

const FALLBACK: RefactoringTypeMeta = {
  label: "Refactoring",
  blurb: "A structural improvement.",
  Icon: Split,
  accentVar: "--color-accent-primary",
};

export function typeMeta(type: RefactoringType | string): RefactoringTypeMeta {
  return TYPE_META[type] ?? FALLBACK;
}

export function typeAccent(type: RefactoringType | string): string {
  return `var(${typeMeta(type).accentVar})`;
}

export const EFFORT_LABEL: Record<EffortBucket, string> = {
  S: "Small",
  M: "Medium",
  L: "Large",
  XL: "Extra large",
};

export const CONFIDENCE_LABEL: Record<Confidence, string> = {
  low: "Low",
  medium: "Medium",
  high: "High",
};

export const CONFIDENCE_DOT: Record<Confidence, string> = {
  high: "bg-[var(--color-success)]",
  medium: "bg-[var(--color-caution)]",
  low: "bg-[var(--color-text-tertiary)]",
};

/** Order types deterministically for legends and tab rows. */
export const TYPE_ORDER: RefactoringType[] = [
  "extract_class",
  "extract_helper",
  "extract_method",
  "move_method",
  "break_cycle",
  "split_file",
];
