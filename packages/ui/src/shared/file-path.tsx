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
import { useCallback, useEffect, useRef, useState } from "react";
import { Check, Copy } from "lucide-react";
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "../ui/tooltip";
import { cn } from "../lib/cn";
import { foldPackagePrefix, pathWithLine, splitDirTail, splitPath } from "../lib/path-parts";

const TOOLTIP_DELAY_MS = 300;

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

interface PieceProps {
  dir: string;
  name: string;
  chip: React.ReactNode;
  line: FilePathProps["line"];
  nameClass: string | undefined;
}

/** Every segment, wrapping only after a slash; the name at reading weight. */
function FullPieces({ dir, name, chip, line, nameClass }: PieceProps) {
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

/**
 * The file name in `name-first`: wider than its column, it wraps rather than
 * losing characters. `break-word` keeps its min-content width, so a table
 * column is not squeezed to one character.
 */
const NAME_CLS = "max-w-full shrink-0 whitespace-normal [overflow-wrap:break-word]";

/** The name first, then the package chip and a directory that clips. */
function NameFirstPieces({ dir, name, chip, line, nameClass }: PieceProps) {
  return (
    <span className="flex min-w-0 items-baseline gap-1.5 whitespace-nowrap">
      <span className={cn(NAME_CLS, nameClass)}>
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

/** Path order on one line: distinguishing labels and middle-ellipsis mentions. */
function InlinePieces({ dir, name, chip, line, nameClass }: PieceProps) {
  return (
    <span className="flex min-w-0 items-baseline whitespace-nowrap">
      {chip ? <span className="mr-1 flex shrink-0">{chip}</span> : null}
      {dir ? <DirClip dir={dir} trailingSlash /> : null}
      {/* One line: the name stays whole and unwrapped; the directory clips first. */}
      <span className={cn("shrink-0 whitespace-nowrap", nameClass)}>{name}</span>
      <LineSuffix line={line} />
    </span>
  );
}

const PIECES: Record<FilePathMode, (props: PieceProps) => React.ReactElement> = {
  full: FullPieces,
  "name-first": NameFirstPieces,
  distinguishing: InlinePieces,
  "middle-ellipsis": InlinePieces,
};

/** What the mode prints: a distinguishing label, or the (optionally folded) path. */
function displayed(path: string, mode: FilePathMode, labels: FilePathProps["labels"], fold: boolean) {
  if (mode === "distinguishing") return { pkg: null, rest: labels?.get(path) ?? splitPath(path).name };
  return fold ? foldPackagePrefix(path) : { pkg: null, rest: path };
}

function Visual({
  path,
  mode,
  line,
  labels,
  fold = false,
  accent,
}: Required<Pick<FilePathProps, "path" | "mode">> &
  Pick<FilePathProps, "line" | "labels" | "fold" | "accent">) {
  const { pkg, rest } = displayed(path, mode, labels, fold);
  const Pieces = PIECES[mode];
  return (
    <Pieces
      {...splitPath(rest)}
      chip={pkg ? <PackageChip pkg={pkg} /> : null}
      line={line}
      nameClass={accent ? ACCENT : undefined}
    />
  );
}

/** True for keyboard focus; an engine without `:focus-visible` counts as keyboard. */
function isKeyboardFocus(el: HTMLElement): boolean {
  try {
    return el.matches(":focus-visible");
  } catch {
    return true;
  }
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
      if (isKeyboardFocus(host)) setOpen(true);
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

/** The copy-path button on its own, for a path rendered inside a link. */
export function CopyPathButton({ text, label, copiedLabel }: { text: string; label: string; copiedLabel: string }) {
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
        "ml-1 inline-flex shrink-0 items-center self-center rounded-sm hover:text-[var(--color-text-primary)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-focus-ring)]",
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
  // The tooltip machinery mounts on first hover or focus, so a list of a
  // thousand paths mounts no Radix provider, root or listener per row, and
  // the path still works in hosts that mount no ambient TooltipProvider.
  const [armed, setArmed] = useState(false);
  const reveal = useCallback((next: boolean) => {
    if (next) setArmed(true);
    setOpen(next);
  }, []);
  // The hover that arms happens before the trigger exists, so Radix never
  // sees it; that hover opens the tooltip itself, after the same delay.
  const hoverTimer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  const cancelHover = useCallback(() => clearTimeout(hoverTimer.current), []);
  const armOnHover = useCallback(() => {
    setArmed(true);
    hoverTimer.current = setTimeout(() => setOpen(true), TOOLTIP_DELAY_MS);
  }, []);
  useEffect(() => cancelHover, [cancelHover]);
  const full = pathWithLine(path, line);
  // `full` shows every character in reading order, and a bare top-level
  // file is already its whole path: neither needs a second, hidden copy.
  const abbreviated = mode !== "full" && (path.includes("/") || full !== path);
  useHostFocus(ref, reveal, abbreviated);

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

  const trigger = (
    <span
      ref={ref}
      className="flex min-w-0 max-w-full"
      onPointerEnter={armed ? undefined : armOnHover}
    >
      <span className="sr-only">{full}</span>
      <span aria-hidden className="flex min-w-0">
        {visual}
      </span>
    </span>
  );

  return (
    // The leave is caught here, on a node arming does not remount, so a pointer
    // that leaves while the trigger moves into the tooltip still cancels it.
    <span
      data-path={path}
      className={cn("inline-flex min-w-0 max-w-full items-baseline font-mono text-xs", className)}
      onPointerLeave={cancelHover}
    >
      {armed ? (
        <TooltipProvider delayDuration={TOOLTIP_DELAY_MS}>
          <Tooltip open={open} onOpenChange={setOpen}>
            <TooltipTrigger asChild>{trigger}</TooltipTrigger>
            <TooltipContent className="max-w-[min(36rem,90vw)] font-mono [overflow-wrap:anywhere]">{full}</TooltipContent>
          </Tooltip>
        </TooltipProvider>
      ) : (
        trigger
      )}
      {copy}
    </span>
  );
}
