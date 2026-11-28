/**
 * Lucide icons for the C4 vocabulary, matching the glyphs the canvas draws: a
 * cylinder for a database, a globe for a network client, a cloud for a hosted
 * service, a package for anything else; containers by what they run as.
 */

import {
  AppWindow,
  Cloud,
  Cog,
  Database,
  Globe,
  Library,
  type LucideIcon,
  Package,
  Puzzle,
  Server,
  SquareTerminal,
} from "lucide-react";

const EXTERNAL_ICON: Record<string, LucideIcon> = {
  db: Database,
  network: Globe,
  service: Cloud,
};

const CONTAINER_ICON: Record<string, LucideIcon> = {
  extension: Puzzle,
  web_app: AppWindow,
  api: Server,
  cli: SquareTerminal,
  worker: Cog,
  library: Library,
};

export function externalIcon(kind: string): LucideIcon {
  return EXTERNAL_ICON[kind] ?? Package;
}

export function containerIcon(kind: string): LucideIcon {
  return CONTAINER_ICON[kind] ?? Package;
}

/** Kinds with a translated label under `zoom.c4.kind` / `zoom.c4.external`. */
export const CONTAINER_KINDS = new Set(Object.keys(CONTAINER_ICON));
export const EXTERNAL_KINDS = new Set(Object.keys(EXTERNAL_ICON));
