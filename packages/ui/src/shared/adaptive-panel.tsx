"use client";

import * as React from "react";
import * as DialogPrimitive from "@radix-ui/react-dialog";
import { X } from "lucide-react";
import { cn } from "../lib/cn";

/**
 * AdaptivePanel — one overlay surface for entity context everywhere.
 *
 * Desktop (md+): right-side slide-in panel.
 * Mobile: bottom sheet with a drag handle; swipe down past the threshold to
 * dismiss.
 *
 * Header is optional — pass `title` for the standard header with a close
 * button, or render your own header inside `children` and set
 * `hideCloseButton`. A DialogTitle is always emitted for screen readers.
 */
export interface AdaptivePanelProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** Accessible name for the panel; rendered in the header unless `hideHeader`. */
  title: React.ReactNode;
  /** Small uppercase label rendered above the title (e.g. entity kind). */
  eyebrow?: React.ReactNode | undefined;
  children: React.ReactNode;
  /** Desktop width classes. Default: `md:max-w-[520px]`. */
  widthClassName?: string | undefined;
  /** Mobile sheet max height. Default: `max-h-[85dvh]`. */
  sheetHeightClassName?: string | undefined;
  hideHeader?: boolean | undefined;
  /**
   * When false, the page stays interactive behind the panel on desktop
   * (no focus trap, overlay only on mobile). Default true.
   */
  modal?: boolean | undefined;
  className?: string | undefined;
}

const SWIPE_DISMISS_PX = 80;

export function AdaptivePanel({
  open,
  onOpenChange,
  title,
  eyebrow,
  children,
  widthClassName = "md:max-w-[520px]",
  sheetHeightClassName = "max-h-[85dvh]",
  hideHeader,
  modal = true,
  className,
}: AdaptivePanelProps) {
  const contentRef = React.useRef<HTMLDivElement>(null);
  const dragStartY = React.useRef<number | null>(null);

  const onTouchStart = (e: React.TouchEvent) => {
    dragStartY.current = e.touches[0]?.clientY ?? null;
  };
  const onTouchMove = (e: React.TouchEvent) => {
    if (dragStartY.current == null || !contentRef.current) return;
    const delta = (e.touches[0]?.clientY ?? 0) - dragStartY.current;
    if (delta > 0) {
      contentRef.current.style.transform = `translateY(${delta}px)`;
      contentRef.current.style.transition = "none";
    }
  };
  const onTouchEnd = (e: React.TouchEvent) => {
    if (dragStartY.current == null || !contentRef.current) return;
    const delta = (e.changedTouches[0]?.clientY ?? 0) - dragStartY.current;
    contentRef.current.style.transform = "";
    contentRef.current.style.transition = "";
    dragStartY.current = null;
    if (delta > SWIPE_DISMISS_PX) onOpenChange(false);
  };

  return (
    <DialogPrimitive.Root open={open} onOpenChange={onOpenChange} modal={modal}>
      <DialogPrimitive.Portal>
        {modal ? (
          <DialogPrimitive.Overlay className="fixed inset-0 z-[var(--z-modal)] bg-black/40 backdrop-blur-[1px] data-[state=open]:animate-in data-[state=closed]:animate-out data-[state=closed]:fade-out-0 data-[state=open]:fade-in-0" />
        ) : (
          open && (
            <div
              className="fixed inset-0 z-[var(--z-modal)] bg-black/40 backdrop-blur-[1px] md:hidden"
              onClick={() => onOpenChange(false)}
              aria-hidden
            />
          )
        )}
        <DialogPrimitive.Content
          ref={contentRef}
          aria-describedby={undefined}
          className={cn(
            "fixed z-[var(--z-modal)] flex flex-col bg-[var(--color-bg-surface)] shadow-2xl",
            "data-[state=open]:animate-in data-[state=closed]:animate-out",
            // Mobile: bottom sheet.
            "inset-x-0 bottom-0 rounded-t-2xl border-t border-[var(--color-border-default)]",
            "data-[state=closed]:slide-out-to-bottom data-[state=open]:slide-in-from-bottom",
            sheetHeightClassName,
            // Desktop: right panel.
            "md:inset-y-0 md:bottom-auto md:left-auto md:right-0 md:h-full md:max-h-none md:w-full",
            "md:rounded-none md:border-t-0 md:border-l",
            "md:data-[state=closed]:slide-out-to-right md:data-[state=open]:slide-in-from-right",
            widthClassName,
            className,
          )}
        >
          <div
            className="flex justify-center py-2 md:hidden"
            onTouchStart={onTouchStart}
            onTouchMove={onTouchMove}
            onTouchEnd={onTouchEnd}
          >
            <div className="h-1 w-10 rounded-full bg-[var(--color-border-hover)]" aria-hidden />
          </div>
          {hideHeader ? (
            <DialogPrimitive.Title className="sr-only">{title}</DialogPrimitive.Title>
          ) : (
            <header className="flex items-start justify-between gap-3 border-b border-[var(--color-border-default)] px-4 pb-3 md:pt-3">
              <div className="min-w-0">
                {eyebrow && (
                  <p className="text-[10px] uppercase tracking-wider text-[var(--color-text-tertiary)]">
                    {eyebrow}
                  </p>
                )}
                <DialogPrimitive.Title className="mt-0.5 break-all font-mono text-[12px] leading-snug text-[var(--color-text-primary)]">
                  {title}
                </DialogPrimitive.Title>
              </div>
              <DialogPrimitive.Close
                aria-label="Close panel"
                className="shrink-0 rounded-md p-1.5 text-[var(--color-text-tertiary)] transition hover:bg-[var(--color-bg-elevated)] hover:text-[var(--color-text-primary)]"
              >
                <X className="h-4 w-4" />
              </DialogPrimitive.Close>
            </header>
          )}
          <div className="flex min-h-0 flex-1 flex-col overflow-y-auto overflow-x-hidden">{children}</div>
        </DialogPrimitive.Content>
      </DialogPrimitive.Portal>
    </DialogPrimitive.Root>
  );
}
