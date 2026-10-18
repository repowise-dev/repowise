"use client";

/**
 * One way to print a repository path. The file name is what a reader
 * recognises, so no mode ever cuts it; the directory gives way first.
 *
 * - `name-first` (lists, tables): name, then the dim directory, which clips
 *   in the middle so its first and last folders stay.
 * - `distinguishing` (compact sets of siblings): the shortest suffix that
 *   tells the set apart, from {@link disambiguateBasenames} computed once by
 *   the caller and passed as `labels`.
 * - `full` (drawers, headers): every segment, wrapping only after a slash.
 * - `middle-ellipsis` (one-line inline mentions): the path in reading order,
 *   clipped in the middle by the box width, never by a character count.
 *
 * Abbreviated modes keep the whole path (and `:line`) as the accessible name
 * and in a tooltip that opens on hover and on keyboard focus of the link,
 * button or row that holds it. Strings are English props so
 * every host app can translate them.
 */

import * as React from "react";
import { useEffect, useRef, useState } from "react";
import { Check, Copy } from "lucide-react";
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "../ui/tooltip";
import { cn } from "../lib/cn";
import { foldPackagePrefix, pathWithLine, splitDirTail, splitPath } from "../lib/path-parts";

export type FilePathMode = "name-first" | "distinguishing" | "full" | "middle-ellipsis";

export interface FilePathProps {
  path: string;
  mode?: FilePathMode;
  /** A line (`42`) or range (`12-40`) printed after the name. */
  line?: number | string | null | undefined;
  /** `distinguishing` only: labels from `disambiguateBasenames(siblings)`, computed once per set. */
  labels?: ReadonlyMap<string, string> | undefined;
  /** Print `packages/<x>/src/` as an `<x>` chip and drop a namespace that repeats it. Display only. */
  fold?: boolean;
  /** A copy-path button beside the path. Not inside a link or button: it is a button itself. */
  copyable?: boolean;
  /** The file name in the link colour, for a path that is a link. */
  accent?: boolean;
  className?: string;
  copyLabel?: string;
  copiedLabel?: string;
}

/** A slash-separated string that may wrap only after its slashes. `<wbr>`, so copying stays clean. */
export function SlashBreaks({ text }: { text: string }) {
  const segments = text.split("/");
  return (
    <>
      {segments.map((segment, i) => (
        <React.Fragment key={i}>
          {segment}
          {i < segments.length - 1 ? (
            <>
              /<wbr />
            </>
          ) : null}
        </React.Fragment>
      ))}
    </>
  );
}

const DIM = "text-[var(--color-text-tertiary)]";
const ACCENT = "text-[var(--color-accent-primary)]";
// Elements that take focus for the path. Not any `[tabindex]`: a dialog's
// `tabindex="-1"` would open every tooltip in a drawer at once.
const HOST = 'a[href],button,summary,[role=option],[role=row],[role=menuitem],[tabindex="0"]';

function PackageChip({ pkg }: { pkg: string }) {
  return (
    <span className={cn("shrink-0 rounded-sm bg-[var(--color-bg-inset)] px-1 text-[10px] leading-4", DIM)}>
      {pkg}
    </span>
  );
}

function LineSuffix({ line }: { line: number | string | null | undefined }) {
  if (line == null || line === "") return null;
  return <span className={cn("tabular-nums", DIM)}>:{line}</span>;
}

/** A directory that clips in the middle: the head gives way, the last folder stays. */
function DirClip({ dir, trailingSlash = false }: { dir: string; trailingSlash?: boolean }) {
  const { head, tail } = splitDirTail(dir);
  const end = trailingSlash ? "/" : "";
  return (
    <>
      {head ? <span className={cn("min-w-0 truncate", DIM)}>{head}/</span> : null}
      <span className={cn("min-w-0 shrink-[0.01] truncate", DIM)}>
        {tail}
        {end}
      </span>
    </>
  );
}

function Visual({
  path,
  mode,
  line,
  labels,
  fold,
  accent,
}: Required<Pick<FilePathProps, "path" | "mode">> &
  Pick<FilePathProps, "line" | "labels" | "fold" | "accent">) {
  const folded = fold ? foldPackagePrefix(path) : { pkg: null, rest: path };
  const chip = folded.pkg ? <PackageChip pkg={folded.pkg} /> : null;
  const { dir, name } = splitPath(mode === "distinguishing" ? labels?.get(path) ?? splitPath(path).name : folded.rest);
  const nameClass = accent ? ACCENT : undefined;

  if (mode === "full") {
    return (
      <span className="[word-break:normal] [overflow-wrap:break-word]">
        {chip ? <span className="mr-1 inline-block">{chip}</span> : null}
        {dir ? (
          <span className={DIM}>
            <SlashBreaks text={`${dir}/`} />
          </span>
        ) : null}
        <span className={cn("font-medium", nameClass)}>{name}</span>
        <LineSuffix line={line} />
      </span>
    );
  }

  if (mode === "name-first") {
    return (
      <span className="flex min-w-0 items-baseline gap-1.5 whitespace-nowrap">
        <span className={cn("max-w-full shrink-0 truncate", nameClass)}>
          {name}
          <LineSuffix line={line} />
        </span>
        {chip}
        {dir ? (
          <span className="flex min-w-0">
            <DirClip dir={dir} />
          </span>
        ) : null}
      </span>
    );
  }

  // distinguishing and middle-ellipsis read in path order.
  return (
    <span className="flex min-w-0 items-baseline whitespace-nowrap">
      {chip ? <span className="mr-1 flex shrink-0">{chip}</span> : null}
      {dir ? <DirClip dir={dir} trailingSlash /> : null}
      <span className={cn("max-w-full shrink-0 truncate", nameClass)}>{name}</span>
      <LineSuffix line={line} />
    </span>
  );
}

/**
 * Opens the tooltip while the link or row holding the path has keyboard focus.
 * A path with no such host stays out of the tab order, so a plain list does
 * not gain a tab stop per row; its full path is still the accessible name.
 * Imperative, so a long list mounts without a re-render per row.
 */
function useHostFocus(
  ref: React.RefObject<HTMLElement | null>,
  setOpen: (open: boolean) => void,
  active: boolean,
) {
  useEffect(() => {
    const el = ref.current;
    if (!active || !el) return;
    const host = el.parentElement?.closest<HTMLElement>(HOST);
    if (!host) return;
    const show = () => {
      let keyboard = true;
      try {
        keyboard = host.matches(":focus-visible");
      } catch {
        /* selector unsupported: treat as keyboard focus */
      }
      if (keyboard) setOpen(true);
    };
    const hide = () => setOpen(false);
    host.addEventListener("focus", show);
    host.addEventListener("blur", hide);
    return () => {
      host.removeEventListener("focus", show);
      host.removeEventListener("blur", hide);
    };
  }, [ref, setOpen, active]);
}

function CopyPathButton({ text, label, copiedLabel }: { text: string; label: string; copiedLabel: string }) {
  const [copied, setCopied] = useState(false);
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1600);
    } catch {
      /* clipboard blocked: the path stays selectable */
    }
  };
  return (
    <button
      type="button"
      onClick={(e) => {
        e.stopPropagation();
        void copy();
      }}
      aria-label={copied ? copiedLabel : label}
      className={cn(
        "ml-1 inline-flex shrink-0 items-center self-center rounded-sm hover:text-[var(--color-text-primary)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-accent-primary)]",
        DIM,
      )}
    >
      {copied ? <Check className="h-3 w-3" aria-hidden /> : <Copy className="h-3 w-3" aria-hidden />}
    </button>
  );
}

export function FilePath({
  path,
  mode = "name-first",
  line,
  labels,
  fold = false,
  copyable = false,
  accent = false,
  className,
  copyLabel = "Copy path",
  copiedLabel = "Copied",
}: FilePathProps) {
  const ref = useRef<HTMLSpanElement>(null);
  const [open, setOpen] = useState(false);
  const full = pathWithLine(path, line);
  // `full` shows every character in reading order, and a bare top-level
  // file is already its whole path: neither needs a second, hidden copy.
  const abbreviated = mode !== "full" && (path.includes("/") || full !== path);
  useHostFocus(ref, setOpen, abbreviated);

  const visual = (
    <Visual path={path} mode={mode} line={line} labels={labels} fold={fold} accent={accent} />
  );
  const copy = copyable ? <CopyPathButton text={path} label={copyLabel} copiedLabel={copiedLabel} /> : null;

  if (!abbreviated) {
    return (
      <span
        data-path={path}
        className={cn("min-w-0 font-mono text-xs", mode === "full" ? "inline" : "inline-flex max-w-full", className)}
      >
        {visual}
        {copy}
      </span>
    );
  }

  return (
    <span data-path={path} className={cn("inline-flex min-w-0 max-w-full items-baseline font-mono text-xs", className)}>
      <TooltipProvider delayDuration={300}>
        <Tooltip open={open} onOpenChange={setOpen}>
          <TooltipTrigger asChild>
            <span ref={ref} className="flex min-w-0 max-w-full">
              <span className="sr-only">{full}</span>
              <span aria-hidden className="flex min-w-0">
                {visual}
              </span>
            </span>
          </TooltipTrigger>
          <TooltipContent className="max-w-[min(36rem,90vw)] font-mono [overflow-wrap:anywhere]">{full}</TooltipContent>
        </Tooltip>
      </TooltipProvider>
      {copy}
    </span>
  );
}
