"use client";

/**
 * The map's own title-row controls after the host's: search, the Map | List
 * switch and the shortcut help button.
 */

import { Keyboard } from "lucide-react";
import { Segmented } from "../../shared/segmented";
import type { SystemMapNavCopy } from "./nav-copy";
import { SystemMapSearch } from "./system-map-search";
import type { MapSearch } from "./use-system-map-search";
import type { MapNavigation } from "./use-map-navigation";

export interface SystemMapNavActionsProps {
  nav: MapNavigation;
  search: MapSearch;
  copy: SystemMapNavCopy;
  name: (serviceId: string) => string;
}

export function SystemMapNavActions({ nav, search, copy, name }: SystemMapNavActionsProps) {
  return (
    <div className="flex min-w-0 flex-wrap items-center gap-2">
      <SystemMapSearch search={search} inputRef={nav.searchRef} copy={copy} name={name} />
      <Segmented
        label={copy.displayLabel}
        value={nav.display}
        options={[
          { value: "map", label: copy.displayMap },
          { value: "list", label: copy.displayList },
        ]}
        onChange={nav.setDisplay}
      />
      <button
        type="button"
        onClick={() => nav.setHelp((open) => !open)}
        aria-label={copy.helpOpen}
        title={copy.helpOpen}
        className="inline-flex h-8 w-8 items-center justify-center rounded-md border border-[var(--color-border-default)] text-[var(--color-text-secondary)] hover:bg-[var(--color-bg-elevated)] hover:text-[var(--color-text-primary)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-focus-ring)]"
      >
        <Keyboard aria-hidden className="h-4 w-4" />
      </button>
    </div>
  );
}
