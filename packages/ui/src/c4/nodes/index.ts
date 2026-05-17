/**
 * `nodeTypes` map for React Flow + re-exports.
 *
 * Node `type` strings map 1:1 to the discriminator in `C4NodeData.kind`,
 * so layout / builder code can drive React Flow with a single field.
 */

import type { NodeTypes } from "@xyflow/react";
import { SystemNode } from "./SystemNode";
import { PersonNode } from "./PersonNode";
import { ExternalSystemNode } from "./ExternalSystemNode";
import { ContainerNode } from "./ContainerNode";
import { ComponentNode } from "./ComponentNode";

export const c4NodeTypes: NodeTypes = {
  system: SystemNode,
  person: PersonNode,
  external: ExternalSystemNode,
  container: ContainerNode,
  component: ComponentNode,
};

export { SystemNode, PersonNode, ExternalSystemNode, ContainerNode, ComponentNode };
