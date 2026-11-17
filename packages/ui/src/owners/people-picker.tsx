"use client";

import * as React from "react";
import { Search, X } from "lucide-react";
import { Input } from "../ui/input";
import { OwnerAvatar } from "./owner-avatar";
import type { KnowledgePerson } from "./knowledge-files";

export interface PeoplePickerProps {
  people: readonly KnowledgePerson[];
  selected: readonly string[];
  onChange: (keys: string[]) => void;
  /** Accessible name and placeholder. */
  label?: string;
  placeholder?: string;
  /** Accessible name of a chip's remove button. */
  removeLabel?: (name: string) => string;
  /** Accessible name of the chip list. */
  selectedLabel?: string;
}

/** Matches listed before the reader narrows the search. */
const MAX_OPTIONS = 8;

/** People not yet picked whose name or email contains the query. */
export function matchPeople(
  people: readonly KnowledgePerson[],
  selected: readonly string[],
  query: string,
  limit = MAX_OPTIONS,
): KnowledgePerson[] {
  const chosen = new Set(selected);
  const needle = query.trim().toLowerCase();
  const hit = (p: KnowledgePerson) =>
    !needle || p.name.toLowerCase().includes(needle) || (p.email ?? "").toLowerCase().includes(needle);
  return people.filter((p) => !chosen.has(p.key) && hit(p)).slice(0, limit);
}

/**
 * Searchable multi-select of people, with avatars. A combobox over a plain
 * list: type to filter by name or email, arrows to move, Enter to add,
 * Backspace on an empty field to drop the last pick.
 */
export function PeoplePicker({
  people,
  selected,
  onChange,
  label = "Pick people",
  placeholder = "Search by name or email…",
  removeLabel = (name) => `Remove ${name}`,
  selectedLabel = "Selected people",
}: PeoplePickerProps) {
  const [query, setQuery] = React.useState("");
  const [open, setOpen] = React.useState(false);
  const [active, setActive] = React.useState(0);
  const listId = React.useId();
  const byKey = React.useMemo(() => new Map(people.map((p) => [p.key, p])), [people]);
  const options = matchPeople(people, selected, query);
  const showList = open && options.length > 0;

  const add = (key: string) => {
    onChange([...selected, key]);
    setQuery("");
    setActive(0);
  };
  const remove = (key: string) => onChange(selected.filter((k) => k !== key));
  const onKeyDown = comboboxKeyHandler({
    count: options.length,
    setActive,
    setOpen,
    onEnter: () => (open && options[active] ? add(options[active]!.key) : false),
    onBackspaceEmpty: () => (query === "" && selected.length > 0 ? remove(selected.at(-1)!) : false),
  });

  return (
    <div className="flex flex-col gap-2">
      <SelectedPeople
        keys={selected}
        byKey={byKey}
        onRemove={remove}
        removeLabel={removeLabel}
        label={selectedLabel}
      />
      <div className="relative max-w-sm">
        <Search className="pointer-events-none absolute left-2.5 top-2.5 h-4 w-4 text-[var(--color-text-tertiary)]" />
        <Input
          role="combobox"
          aria-label={label}
          aria-expanded={showList}
          aria-controls={listId}
          aria-autocomplete="list"
          {...(showList ? { "aria-activedescendant": `${listId}-${active}` } : {})}
          value={query}
          placeholder={placeholder}
          className="pl-8"
          onChange={(e) => {
            setQuery(e.target.value);
            setOpen(true);
            setActive(0);
          }}
          onFocus={() => setOpen(true)}
          // Options cancel mousedown, so a click on one never blurs the field first.
          onBlur={() => setOpen(false)}
          onKeyDown={onKeyDown}
        />
        {showList && (
          <PeopleOptions id={listId} options={options} active={active} onHover={setActive} onPick={add} />
        )}
      </div>
    </div>
  );
}

/** Keys that move or pick, whose browser default (caret move, form submit) must not run. */
const CONSUMED_KEYS = new Set(["ArrowDown", "ArrowUp", "Enter"]);

/** Arrow, Enter, Escape and Backspace handling for a combobox over `count` options. */
function comboboxKeyHandler({
  count,
  setActive,
  setOpen,
  onEnter,
  onBackspaceEmpty,
}: {
  count: number;
  setActive: React.Dispatch<React.SetStateAction<number>>;
  setOpen: (open: boolean) => void;
  /** Returns false when there was nothing to pick. */
  onEnter: () => void | false;
  onBackspaceEmpty: () => void | false;
}) {
  return (e: React.KeyboardEvent<HTMLInputElement>) => {
    const keys: Record<string, () => void | false> = {
      ArrowDown: () => {
        setOpen(true);
        setActive((i) => Math.min(i + 1, Math.max(count - 1, 0)));
      },
      ArrowUp: () => setActive((i) => Math.max(i - 1, 0)),
      Enter: onEnter,
      Escape: () => setOpen(false),
      Backspace: onBackspaceEmpty,
    };
    const handled = keys[e.key]?.() !== false;
    if (handled && CONSUMED_KEYS.has(e.key)) e.preventDefault();
  };
}

function SelectedPeople({
  keys,
  byKey,
  onRemove,
  removeLabel,
  label,
}: {
  keys: readonly string[];
  byKey: ReadonlyMap<string, KnowledgePerson>;
  onRemove: (key: string) => void;
  removeLabel: (name: string) => string;
  label: string;
}) {
  if (keys.length === 0) return null;
  return (
    <ul aria-label={label} className="flex flex-wrap gap-1.5">
      {keys.map((key) => {
        const p = byKey.get(key) ?? { key, name: key, email: null };
        return (
          <li
            key={key}
            className="inline-flex items-center gap-1.5 rounded-full border border-[var(--color-border-default)] bg-[var(--color-bg-surface)] py-0.5 pl-0.5 pr-1 text-xs text-[var(--color-text-primary)]"
          >
            <OwnerAvatar name={p.name} email={p.email} size="sm" />
            {p.name}
            <button
              type="button"
              onClick={() => onRemove(key)}
              aria-label={removeLabel(p.name)}
              className="rounded-full p-0.5 text-[var(--color-text-tertiary)] hover:text-[var(--color-text-primary)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-focus-ring)]"
            >
              <X className="h-3 w-3" />
            </button>
          </li>
        );
      })}
    </ul>
  );
}

function PeopleOptions({
  id,
  options,
  active,
  onHover,
  onPick,
}: {
  id: string;
  options: readonly KnowledgePerson[];
  active: number;
  onHover: (index: number) => void;
  onPick: (key: string) => void;
}) {
  return (
    <ul
      id={id}
      role="listbox"
      className="absolute z-20 mt-1 max-h-72 w-full overflow-auto rounded-md border border-[var(--color-border-default)] bg-[var(--color-bg-overlay)] py-1 shadow-lg"
    >
      {options.map((p, i) => (
        <li
          key={p.key}
          id={`${id}-${i}`}
          role="option"
          aria-selected={i === active}
          onMouseDown={(e) => e.preventDefault()}
          onClick={() => onPick(p.key)}
          onMouseEnter={() => onHover(i)}
          className={`flex cursor-pointer items-center gap-2 px-2.5 py-1.5 text-sm ${
            i === active ? "bg-[var(--color-bg-elevated)]" : ""
          }`}
        >
          <OwnerAvatar name={p.name} email={p.email} size="sm" />
          <span className="min-w-0">
            <span className="block text-[var(--color-text-primary)]">{p.name}</span>
            {p.email && p.email !== p.name && (
              <span className="block truncate text-xs text-[var(--color-text-tertiary)]" title={p.email}>
                {p.email}
              </span>
            )}
          </span>
        </li>
      ))}
    </ul>
  );
}
