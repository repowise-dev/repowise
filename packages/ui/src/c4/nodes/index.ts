import type { NodeTypes } from "@xyflow/react";
import { SystemNode } from "./SystemNode";
import { PersonNode } from "./PersonNode";
import { ExternalSystemNode } from "./ExternalSystemNode";
import { ContainerNode } from "./ContainerNode";
import { ComponentNode } from "./ComponentNode";
import { ArchFileNode } from "./ArchFileNode";
import { ArchContainerNode } from "./ArchContainerNode";
import { LayerClusterNode } from "./LayerClusterNode";
import { PortalNode } from "./PortalNode";
import { ScopeFrameNode } from "./ScopeFrameNode";

export const c4NodeTypes: NodeTypes = {
  system: SystemNode,
  person: PersonNode,
  external: ExternalSystemNode,
  container: ContainerNode,
  component: ComponentNode,
};

export const archNodeTypes: NodeTypes = {
  ...c4NodeTypes,
  archFile: ArchFileNode,
  archContainer: ArchContainerNode,
  layerCluster: LayerClusterNode,
  // Curated sub-group card — same component, "subGroup" kind via data.
  subGroupCluster: LayerClusterNode,
  portal: PortalNode,
  // Dashed "you are here" boundary behind the drilled tier (kg-ux §2.4).
  scopeFrame: ScopeFrameNode,
};

export { SystemNode, PersonNode, ExternalSystemNode, ContainerNode, ComponentNode };
export { ArchFileNode, ArchContainerNode, LayerClusterNode, PortalNode, ScopeFrameNode };
