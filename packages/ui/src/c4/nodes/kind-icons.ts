import {
  ArrowRight,
  Box,
  Boxes,
  Braces,
  Cog,
  Database,
  ExternalLink,
  FileCode,
  FileJson,
  FileText,
  Folder,
  Globe,
  Layers,
  Lightbulb,
  Package,
  Server,
  Table,
  User,
  Workflow,
  type LucideIcon,
} from "lucide-react";

/**
 * Kind → glyph map for the blueprint ink nodes (kg-ux plan §2.2): with the
 * tone-rainbow gone from card faces, the ICON carries the node's type.
 * One map so cards, filters, and the legend can never disagree.
 */
const KIND_ICONS: Record<string, LucideIcon> = {
  file: FileCode,
  function: Braces,
  class: Box,
  module: Package,
  config: Cog,
  document: FileText,
  concept: Lightbulb,
  service: Server,
  resource: Database,
  pipeline: Workflow,
  table: Table,
  endpoint: Globe,
  schema: FileJson,
  system: Boxes,
  person: User,
  external: ExternalLink,
  container: Folder,
  component: Box,
  layer: Layers,
  subGroup: Folder,
  portal: ArrowRight,
};

export function getKindIcon(kind: string): LucideIcon {
  return KIND_ICONS[kind] ?? FileCode;
}
