"use client";

import { cn } from "../lib/cn";

/**
 * Deterministic colored initials avatar. Falls back to a single character
 * if we only have an email and no name. Kept self-contained so the hosted
 * frontend can drop it in without bringing a real avatar service.
 */
const PALETTE = [
  "bg-rose-500/20 text-rose-300",
  "bg-amber-500/20 text-amber-300",
  "bg-emerald-500/20 text-emerald-300",
  "bg-sky-500/20 text-sky-300",
  "bg-indigo-500/20 text-indigo-300",
  "bg-fuchsia-500/20 text-fuchsia-300",
  "bg-orange-500/20 text-orange-300",
  "bg-teal-500/20 text-teal-300",
];

function hash(s: string): number {
  let h = 0;
  for (let i = 0; i < s.length; i++) h = (h * 31 + s.charCodeAt(i)) | 0;
  return Math.abs(h);
}

function initials(name: string, email: string | null): string {
  const source = (name || email || "?").trim();
  if (!source) return "?";
  const parts = source.split(/\s+/).filter(Boolean);
  if (parts.length >= 2) {
    const a = parts[0]?.[0] ?? "";
    const b = parts[1]?.[0] ?? "";
    return (a + b).toUpperCase() || "?";
  }
  return source.slice(0, 2).toUpperCase();
}

export interface OwnerAvatarProps {
  name: string;
  email?: string | null;
  size?: "sm" | "md" | "lg";
  className?: string;
}

export function OwnerAvatar({ name, email, size = "md", className }: OwnerAvatarProps) {
  const key = (email ?? name ?? "?").toLowerCase();
  const color = PALETTE[hash(key) % PALETTE.length];
  const sizeCls =
    size === "sm" ? "h-6 w-6 text-[10px]" : size === "lg" ? "h-12 w-12 text-base" : "h-8 w-8 text-xs";
  return (
    <div
      className={cn(
        "inline-flex shrink-0 items-center justify-center rounded-full font-semibold tracking-wide",
        color,
        sizeCls,
        className,
      )}
      aria-label={name || email || "contributor"}
      title={name || email || ""}
    >
      {initials(name, email ?? null)}
    </div>
  );
}
