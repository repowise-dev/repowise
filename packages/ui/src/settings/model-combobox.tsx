"use client";

/**
 * Searchable model picker that also takes any typed id. Used by the provider
 * settings form (as a dropdown) and the chat model menu (inline, inside its
 * popover). Follows the ARIA combobox pattern: the input owns focus and
 * `aria-activedescendant` names the highlighted option.
 */

import { useId, useMemo, useState, type KeyboardEvent, type ReactNode } from "react";
import { Check } from "lucide-react";
import { FIELD_CLASS } from "../ui/input";
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

interface ModelOption extends ModelChoice {
  /** The typed text offered as an id of its own. */
  typed: boolean;
}

/** Models matching *typed*, plus the typed text itself when no id equals it. */
function useModelOptions(models: ModelChoice[], typed: string): ModelOption[] {
  return useMemo(() => {
    const needle = typed.toLowerCase();
    const matches = needle
      ? models.filter((m) => `${m.id} ${m.label ?? ""}`.toLowerCase().includes(needle))
      : models;
    const rows: ModelOption[] = matches.map((m) => ({ ...m, typed: false }));
    if (typed && !models.some((m) => m.id === typed)) {
      rows.push({ id: typed, label: null, typed: true });
    }
    return rows;
  }, [models, typed]);
}

interface ComboboxState {
  expanded: boolean;
  /** null while the user has not typed: the input then shows the value. */
  query: string | null;
  typed: string;
  active: number;
  options: ModelOption[];
  listId: string;
  optionId: (index: number) => string;
  setActive: (index: number) => void;
  type: (text: string) => void;
  show: () => void;
  hide: () => void;
  choose: (model: string) => void;
  onKeyDown: (event: KeyboardEvent<HTMLInputElement>) => void;
}

type KeyAction = (state: ComboboxState, event: KeyboardEvent<HTMLInputElement>) => void;

function moveBy(step: number): KeyAction {
  return (state, event) => {
    event.preventDefault();
    if (!state.expanded) state.show();
    const count = state.options.length;
    if (count === 0) return;
    const next = (state.active + step + count) % count;
    state.setActive(next);
    document.getElementById(state.optionId(next))?.scrollIntoView?.({ block: "nearest" });
  };
}

const KEY_ACTIONS: Record<string, KeyAction> = {
  ArrowDown: moveBy(1),
  ArrowUp: moveBy(-1),
  Enter: (state, event) => {
    const picked = state.options[state.active]?.id ?? (state.typed || null);
    if (!picked || !state.expanded) return;
    event.preventDefault();
    state.choose(picked);
  },
};

function useComboboxState({
  models,
  inline,
  onOpen,
  onSelect,
}: {
  models: ModelChoice[];
  inline: boolean;
  onOpen: (() => void) | undefined;
  onSelect: (model: string) => void;
}): ComboboxState {
  const listId = useId();
  const [open, setOpen] = useState(inline);
  const [query, setQuery] = useState<string | null>(null);
  const [active, setActive] = useState(-1);
  const typed = (query ?? "").trim();
  const options = useModelOptions(models, typed);
  const expanded = inline || open;

  function show() {
    if (!open) onOpen?.();
    setOpen(true);
  }

  function reset() {
    setQuery(null);
    setActive(-1);
  }

  function hide() {
    if (inline) return;
    setOpen(false);
    reset();
  }

  function choose(model: string) {
    onSelect(model);
    reset();
    if (!inline) setOpen(false);
  }

  function type(text: string) {
    setQuery(text);
    setActive(-1);
    show();
  }

  const state: ComboboxState = {
    expanded,
    query,
    typed,
    active,
    options,
    listId,
    optionId: (index) => `${listId}-${index}`,
    setActive,
    type,
    show,
    hide,
    choose,
    onKeyDown: (event) => {
      if (event.key === "Escape" && open && !inline) {
        // Close the list only; an enclosing dialog stays open.
        event.preventDefault();
        event.stopPropagation();
        hide();
        return;
      }
      KEY_ACTIONS[event.key]?.(state, event);
    },
  };
  return state;
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
  const state = useComboboxState({ models, inline, onOpen, onSelect });
  const { active, options } = state;

  return (
    <div className={cn("relative", className)}>
      <input
        id={id}
        type="text"
        role="combobox"
        aria-label={ariaLabel}
        aria-expanded={state.expanded}
        aria-controls={state.listId}
        aria-autocomplete="list"
        aria-activedescendant={active >= 0 ? state.optionId(active) : undefined}
        autoComplete="off"
        spellCheck={false}
        autoFocus={autoFocus}
        placeholder={inline ? labels.placeholder : value || labels.placeholder}
        value={state.query ?? (inline ? "" : value)}
        onChange={(event) => state.type(event.target.value)}
        onFocus={inline ? undefined : state.show}
        onClick={inline ? undefined : state.show}
        onBlur={state.hide}
        onKeyDown={state.onKeyDown}
        className={cn(
          FIELD_CLASS,
          "box-border flex h-8 w-full min-w-0 px-2.5 font-mono text-xs",
        )}
      />
      {state.expanded && (
        <ul
          id={state.listId}
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
          {options.map((option, index) => (
            <OptionRow
              key={`${option.typed ? "typed:" : ""}${option.id}`}
              id={state.optionId(index)}
              option={option}
              selected={!option.typed && option.id === value}
              highlighted={index === active}
              isDefault={!option.typed && option.id === defaultModel}
              labels={labels}
              onChoose={() => state.choose(option.id)}
              onHover={() => state.setActive(index)}
            />
          ))}
          {loading && (
            <StatusRow>
              <Spinner size="sm" />
              {labels.loading}
            </StatusRow>
          )}
          {!loading && options.length === 0 && <StatusRow>{labels.noMatch}</StatusRow>}
        </ul>
      )}
    </div>
  );
}

function OptionRow({
  id,
  option,
  selected,
  highlighted,
  isDefault,
  labels,
  onChoose,
  onHover,
}: {
  id: string;
  option: ModelOption;
  selected: boolean;
  highlighted: boolean;
  isDefault: boolean;
  labels: ModelComboboxLabels;
  onChoose: () => void;
  onHover: () => void;
}) {
  return (
    <li
      id={id}
      role="option"
      aria-selected={selected}
      // mousedown, so the input's blur does not close the list first.
      onMouseDown={(event) => {
        event.preventDefault();
        onChoose();
      }}
      onMouseEnter={onHover}
      className={cn(
        "flex cursor-pointer items-center gap-2 px-2.5 py-1.5 text-xs",
        highlighted
          ? "bg-[var(--color-bg-elevated)] text-[var(--color-text-primary)]"
          : "text-[var(--color-text-secondary)]",
      )}
    >
      <span className="min-w-0 flex-1 truncate font-mono" title={option.id}>
        {option.typed ? labels.useTyped(option.id) : option.id}
      </span>
      {option.label && option.label !== option.id && (
        <span className="truncate text-[var(--color-text-tertiary)]">{option.label}</span>
      )}
      {isDefault && (
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
}

function StatusRow({ children }: { children: ReactNode }) {
  return (
    <li
      role="presentation"
      className="flex items-center gap-2 px-2.5 py-1.5 text-xs text-[var(--color-text-tertiary)]"
    >
      {children}
    </li>
  );
}
