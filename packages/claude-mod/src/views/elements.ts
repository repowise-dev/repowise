/**
 * Views build plain trees with these builders, so they stay pure and testable
 * without a surface. register.ts turns a tree into real elements with
 * `materialize` over the table `$.ui.resolve(e)` returns.
 */

export interface TextProps {
  dimColor?: boolean;
  wrap?: "wrap" | "truncate" | "truncate-start" | "truncate-middle" | "truncate-end";
}

export interface BoxProps {
  key?: string;
  flexDirection?: "row" | "column";
}

export type Node =
  | { type: "Text"; props: TextProps; children: string[] }
  | { type: "Box"; props: BoxProps; children: Node[] };

export function text(value: string, props: TextProps = {}): Node {
  return { type: "Text", props, children: [value] };
}

export function box(props: BoxProps, children: Node[]): Node {
  return { type: "Box", props, children };
}

export type ElementTable = Record<string, (props: Record<string, unknown>) => unknown>;

export function materialize(node: Node, table: ElementTable): unknown {
  const build = table[node.type];
  if (!build) throw new Error(`element ${node.type} is not on this surface`);
  const children =
    node.type === "Text" ? node.children : node.children.map((child) => materialize(child, table));
  return build({ ...node.props, children });
}
