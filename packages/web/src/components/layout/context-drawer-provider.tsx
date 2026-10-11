"use client";

import * as React from "react";
import { useRouter, useSearchParams } from "next/navigation";
import {
  ContextDrawer,
  ContextDrawerProvider,
  type ContextDrawerTab,
} from "@repowise-dev/ui/shared/context-drawer";
import type { EntityKind, EntityRef } from "@repowise-dev/ui/shared/entity";

const ENTITY_KINDS: EntityKind[] = ["file", "symbol", "decision", "owner", "commit"];

function parseDrawerParam(raw: string | null): EntityRef | null {
  if (!raw) return null;
  // Format: "kind:id" — kind must be one of EntityKind. id is URL-decoded.
  const sep = raw.indexOf(":");
  if (sep === -1) return null;
  const kind = raw.slice(0, sep) as EntityKind;
  if (!ENTITY_KINDS.includes(kind)) return null;
  const id = decodeURIComponent(raw.slice(sep + 1));
  return { kind, id };
}

function formatDrawerParam(entity: EntityRef): string {
  return `${entity.kind}:${encodeURIComponent(entity.id)}`;
}

interface ProviderProps {
  children: React.ReactNode;
  tabs?: ContextDrawerTab[];
}

/**
 * Client wrapper that mounts the ContextDrawer in the app shell and keeps the
 * open entity URL-synced via `?drawer=kind:id`. Active repoId (when present
 * in the path) is attached to the entity so route resolvers work correctly.
 */
export function ContextDrawerShell({ children, tabs }: ProviderProps) {
  const router = useRouter();
  const searchParams = useSearchParams();

  // Hydrate from URL (?drawer=kind:id). Keep a memoized derived value to
  // avoid re-creating EntityRef on every render.
  const initial = React.useMemo<EntityRef | null>(() => {
    const parsed = parseDrawerParam(searchParams.get("drawer"));
    if (!parsed) return null;
    if (typeof window !== "undefined") {
      const m = window.location.pathname.match(/^\/repos\/([^/]+)/);
      if (m && m[1]) return { ...parsed, repoId: m[1] };
    }
    return parsed;
  }, [searchParams]);

  const handleEntityChange = React.useCallback(
    (next: EntityRef | null) => {
      const sp = new URLSearchParams(searchParams.toString());
      if (next) sp.set("drawer", formatDrawerParam(next));
      else sp.delete("drawer");
      const qs = sp.toString();
      router.replace(qs ? `?${qs}` : window.location.pathname, { scroll: false });
    },
    [router, searchParams],
  );

  return (
    <ContextDrawerProvider initialEntity={initial} onEntityChange={handleEntityChange}>
      {children}
      <ContextDrawer tabs={tabs} />
    </ContextDrawerProvider>
  );
}
