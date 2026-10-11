"use client";

/**
 * One axis, one control: a radiogroup of mutually exclusive options, with
 * roving focus and arrow keys. Shared so every lens switch in the product
 * behaves and reads the same way.
 */

import { useRef, useState, type KeyboardEvent as ReactKeyboardEvent } from "react";

export interface SegmentOption<T extends string> {
  value: T;
  label: string;
  /** Figure after the label, e.g. a finding count. */
  count?: string | undefined;
  /** Set to disable the option; the reason is its tooltip and accessible description. */
  disabledReason?: string | undefined;
  hint?: string | undefined;
}

/**
 * One axis, one control: a radiogroup of mutually exclusive options. A
 * disabled option stays visible with its reason rather than vanishing, so the
 * reader learns the lens exists and why it has nothing to show.
 */
export function Segmented<T extends string>({
  label,
  value,
  options,
  onChange,
}: {
  label: string;
  value: T;
  options: SegmentOption<T>[];
  onChange: (value: T) => void;
}) {
  const refs = useRef<(HTMLButtonElement | null)[]>([]);
  const [reason, setReason] = useState<string | null>(null);
  // The roving tab stop: the active option, or the first enabled one when the
  // active option is disabled, so the group never drops out of the tab order.
  const activeIndex = options.findIndex((o) => o.value === value && !o.disabledReason);
  const tabStop = activeIndex >= 0 ? activeIndex : Math.max(options.findIndex((o) => !o.disabledReason), 0);

  // Arrows walk every option, disabled ones included, so a keyboard reader can
  // reach a disabled lens and hear why; only enabled ones are selected.
  const onKeyDown = (e: ReactKeyboardEvent) => {
    const i = refs.current.findIndex((el) => el === document.activeElement);
    const from = i >= 0 ? i : tabStop;
    let next: number;
    if (e.key === "ArrowRight" || e.key === "ArrowDown") next = (from + 1) % options.length;
    else if (e.key === "ArrowLeft" || e.key === "ArrowUp") next = (from - 1 + options.length) % options.length;
    else return;
    e.preventDefault();
    refs.current[next]?.focus();
    const target = options[next];
    if (target && !target.disabledReason) onChange(target.value);
  };

  return (
    <div className="flex min-w-0 flex-col gap-1">
      <div
        role="radiogroup"
        aria-label={label}
        onKeyDown={onKeyDown}
        className="inline-flex max-w-full flex-wrap self-start rounded-md border border-[var(--color-border-default)] p-0.5"
      >
        {options.map((o, i) => {
          const active = o.value === value && !o.disabledReason;
          const reasonId = o.disabledReason ? `seg-reason-${label}-${o.value}`.replace(/\s+/g, "-") : undefined;
          const show = o.disabledReason ? () => setReason(o.disabledReason ?? null) : undefined;
          return (
            <button
              key={o.value}
              ref={(el) => {
                refs.current[i] = el;
              }}
              type="button"
              role="radio"
              aria-checked={active}
              aria-disabled={o.disabledReason ? true : undefined}
              aria-describedby={reasonId}
              tabIndex={i === tabStop ? 0 : -1}
              title={o.disabledReason ?? o.hint}
              onClick={() => (o.disabledReason ? show?.() : onChange(o.value))}
              onMouseEnter={show}
              onFocus={show}
              onMouseLeave={o.disabledReason ? () => setReason(null) : undefined}
              onBlur={o.disabledReason ? () => setReason(null) : undefined}
              className={`inline-flex items-center gap-1.5 rounded px-2.5 py-1 text-xs transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-accent-primary)] ${
                active
                  ? "bg-[var(--color-bg-elevated)] font-semibold text-[var(--color-text-primary)]"
                  : o.disabledReason
                    ? "cursor-not-allowed font-medium text-[var(--color-text-tertiary)]"
                    : "font-medium text-[var(--color-text-secondary)] hover:text-[var(--color-text-primary)]"
              }`}
            >
              {o.label}
              {o.count !== undefined && (
                <span className="font-mono text-[10px] tabular-nums text-[var(--color-text-tertiary)]">{o.count}</span>
              )}
              {reasonId && (
                <span id={reasonId} className="sr-only">
                  {o.disabledReason}
                </span>
              )}
            </button>
          );
        })}
      </div>
      {reason && <p className="text-xs text-[var(--color-text-tertiary)]">{reason}</p>}
    </div>
  );
}
