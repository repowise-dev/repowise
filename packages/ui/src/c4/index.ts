/**
 * @repowise-dev/ui/c4 — shared C4 diagram surface.
 *
 * Public entry point. Host pages compose `<C4Diagram>` with data fetched
 * from `/api/graph/{repo_id}/c4/{l1,l2,l3}` and either lift state up via
 * props OR drive it locally with `useC4Store`.
 */

export { C4Diagram } from "./C4Diagram";
export type { C4DiagramProps } from "./C4Diagram";

export { useC4Store } from "./store/use-c4-store";
export type { C4Store, C4StoreState, UseC4StoreOptions } from "./store/use-c4-store";

export { useC4Layout } from "./hooks/use-c4-layout";
export type { C4LayoutResult } from "./hooks/use-c4-layout";
export { useC4Keyboard } from "./hooks/use-c4-keyboard";

export { computeC4Layout, C4_NODE_SIZES } from "./layout/elk-c4-layout";
export type {
  C4LayoutNode,
  C4LayoutEdge,
  C4LayoutPosition,
} from "./layout/elk-c4-layout";

export { c4NodeTypes } from "./nodes";
export {
  SystemNode,
  PersonNode,
  ExternalSystemNode,
  ContainerNode,
  ComponentNode,
} from "./nodes";

export { RelationEdge, c4EdgeTypes } from "./edges/RelationEdge";

export {
  C4LevelTabs,
  C4Breadcrumb,
  C4Legend,
  C4NodeInspector,
  C4DetailPanel,
} from "./panels";
export type { C4DetailPanelProps, C4Health, C4DocSummary } from "./panels";

export type {
  C4Level,
  C4Category,
  C4Person,
  C4System,
  C4ExternalSystem,
  C4Container,
  C4Component,
  C4Relation,
  C4L1,
  C4L2,
  C4L3,
  C4NodeData,
  C4EdgeData,
} from "./types";
