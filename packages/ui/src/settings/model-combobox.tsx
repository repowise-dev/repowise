"use client";

/**
 * Searchable model picker that also takes any typed id. Used by the provider
 * settings form (as a dropdown) and the chat model menu (inline, inside its
 * popover). Follows the ARIA combobox pattern: the input owns focus and
 * `aria-activedescendant` names the highlighted option.
 */

import { useId, useMemo, useState, type KeyboardEvent } from "react";
import { Check } from "lucide-react";
import { Spinner } from "../ui/spinner";
import { cn } from "../lib/cn";

export interface ModelChoice {
  id: string;
  label?: string | null;
}

export interface ModelComboboxLabels {
  placeholder: string;
  /** The row that picks the typed text as a model id. */
  useTyped: (id: string) => string;
  /** Tag beside the default model. */
  defaultTag: string;
  loading: string;
  noMatch: string;
}

const DEFAULT_LABELS: ModelComboboxLabels = {
  placeholder: "Search or type a model id",
  useTyped: (id) => `Use "${id}"`,
  defaultTag: "default",
  loading: "Loading models",
  noMatch: "No listed model matches.",
};

export interface ModelComboboxProps {
  value: string;
  onSelect: (model: string) => void;
  models: ModelChoice[];
  /** Marked "default" in the list. */
  defaultModel?: string | null;
  loading?: boolean;
  /** Called each time the list opens, so a consumer can fetch lazily. */
  onOpen?: () => void;
  /** Keep the list open below the input (inside a popover). */
  inline?: boolean;
  id?: string;
  ariaLabel?: string;
  autoFocus?: boolean;
  labels?: Partial<ModelComboboxLabels>;
  className?: string;
}

export function ModelCombobox({
  value,
  onSelect,
  models,
  defaultModel,
  loading = false,
  onOpen,
  inline = false,
  id,
  ariaLabel,
  autoFocus,
  labels: labelOverrides,
  className,
}: ModelComboboxProps) {
  const labels = { ...DEFAULT_LABELS, ...labelOverrides };
  const listId = useId();
  const [open, setOpen] = useState(inline);
  // null while the user has not typed: the input then shows the value.
  const [query, setQuery] = useState<string | null>(null);
  const [active, setActive] = useState(-1);

  const typed = (query ?? "").trim();
  const options = useMemo(() => {
    const needle = typed.toLowerCase();
    const matches = needle
      ? models.filter((m) =>
          `${m.id} ${m.label ?? ""}`.toLowerCase().includes(needle),
        )
      : models;
    const rows = matches.map((m) => ({ ...m, typed: false }));
    if (typed && !models.some((m) => m.id === typed)) {
      rows.push({ id: typed, label: null, typed: true });
    }
    return rows;
  }, [models, typed]);

  const expanded = inline || open;
  const optionId = (index: number) => `${listId}-${index}`;

  function show() {
    if (!open) onOpen?.();
    setOpen(true);
  }

  function hide() {
    if (inline) return;
    setOpen(false);
    setQuery(null);
    setActive(-1);
  }

  function choose(model: string) {
    onSelect(model);
    setQuery(null);
    setActive(-1);
    if (!inline) setOpen(false);
  }

  function move(step: number) {
    if (!expanded) show();
    if (options.length === 0) return;
    const next = (active + step + options.length) % options.length;
    setActive(next);
    document.getElementById(optionId(next))?.scrollIntoView?.({ block: "nearest" });
  }

  function onKeyDown(event: KeyboardEvent<HTMLInputElement>) {
    if (event.key === "ArrowDown") {
      event.preventDefault();
      move(1);
    } else if (event.key === "ArrowUp") {
      event.preventDefault();
      move(-1);
    } else if (event.key === "Enter") {
      const picked = options[active]?.id ?? (typed || null);
      if (picked && expanded) {
        event.preventDefault();
        choose(picked);
      }
    } else if (event.key === "Escape" && open && !inline) {
      // Close the list only; an enclosing dialog stays open.
      event.preventDefault();
      event.stopPropagation();
      hide();
    }
  }

  return (
    <div className={cn("relative", className)}>
      <input
        id={id}
        type="text"
        role="combobox"
        aria-label={ariaLabel}
        aria-expanded={expanded}
        aria-controls={listId}
        aria-autocomplete="list"
        aria-activedescendant={active >= 0 ? optionId(active) : undefined}
        autoComplete="off"
        spellCheck={false}
        autoFocus={autoFocus}
        placeholder={inline ? labels.placeholder : value || labels.placeholder}
        value={query ?? (inline ? "" : value)}
        onChange={(event) => {
          setQuery(event.target.value);
          setActive(-1);
          show();
        }}
        onFocus={inline ? undefined : show}
        onClick={inline ? undefined : show}
        onBlur={hide}
        onKeyDown={onKeyDown}
        className={cn(
          "box-border flex h-8 w-full min-w-0 rounded-md border border-[var(--color-border-default)] bg-[var(--color-bg-surface)] px-2.5 font-mono text-xs text-[var(--color-text-primary)]",
          "placeholder:text-[var(--color-text-tertiary)] focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-[var(--color-accent-primary)]",
        )}
      />
      {expanded && (
        <ul
          id={listId}
          role="listbox"
          aria-label={ariaLabel}
          aria-busy={loading || undefined}
          className={cn(
            "max-h-64 overflow-y-auto py-1",
            inline
              ? "mt-1"
              : "absolute left-0 right-0 top-full z-[var(--z-dropdown)] mt-1 rounded-md border border-[var(--color-border-default)] bg-[var(--color-bg-overlay)] shadow-[var(--shadow-lg)]",
          )}
        >
          {options.map((option, index) => {
            const selected = !option.typed && option.id === value;
            return (
              <li
                key={`${option.typed ? "typed:" : ""}${option.id}`}
                id={optionId(index)}
                role="option"
                aria-selected={selected}
                // mousedown, so the input's blur does not close the list first.
                onMouseDown={(event) => {
                  event.preventDefault();
                  choose(option.id);
                }}
                onMouseEnter={() => setActive(index)}
                className={cn(
                  "flex cursor-pointer items-center gap-2 px-2.5 py-1.5 text-xs",
                  index === active
                    ? "bg-[var(--color-bg-elevated)] text-[var(--color-text-primary)]"
                    : "text-[var(--color-text-secondary)]",
                )}
              >
                <span className="min-w-0 flex-1 truncate font-mono" title={option.id}>
                  {option.typed ? labels.useTyped(option.id) : option.id}
                </span>
                {option.label && option.label !== option.id && (
                  <span className="truncate text-[var(--color-text-tertiary)]">
                    {option.label}
                  </span>
                )}
                {!option.typed && option.id === defaultModel && (
                  <span className="shrink-0 text-[10px] text-[var(--color-text-tertiary)]">
                    {labels.defaultTag}
                  </span>
                )}
                {selected && (
                  <Check
                    aria-hidden="true"
                    className="h-3 w-3 shrink-0 text-[var(--color-accent-primary)]"
                  />
                )}
              </li>
            );
          })}
          {loading && (
            <li
              role="presentation"
              className="flex items-center gap-2 px-2.5 py-1.5 text-xs text-[var(--color-text-tertiary)]"
            >
              <Spinner size="sm" />
              {labels.loading}
            </li>
          )}
          {!loading && options.length === 0 && (
            <li
              role="presentation"
              className="px-2.5 py-1.5 text-xs text-[var(--color-text-tertiary)]"
            >
              {labels.noMatch}
            </li>
          )}
        </ul>
      )}
    </div>
  );
}
