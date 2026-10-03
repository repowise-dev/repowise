/**
 * Views build plain trees with these builders, so they stay pure and testable
 * without a surface. register.ts turns a tree into real elements with
 * `materialize` over the table `$.ui.resolve(e)` returns.
 */

export interface TextProps {
  dimColor?: boolean;
  wrap?: "truncate-end";
  /** A theme key (`success`, `warning`, `error`): the terminal's own colors for health. */
  color?: string;
}

export interface BoxProps {
  key?: string;
  flexDirection?: "column" | "row";
  columnGap?: number;
}

/** A pressable leaf; `key` names the action register.ts runs on a press. */
export interface ButtonProps {
  key: string;
  label: string;
  hotkey: string;
  /** Drawn as `1: label`, the way a survey's rows read. */
  plain: true;
}

export type Node =
  | { type: "Text"; props: TextProps; children: string[] }
  | { type: "Box"; props: BoxProps; children: Node[] }
  | { type: "Button"; props: ButtonProps };

export function text(value: string, props: TextProps = {}): Node {
  return { type: "Text", props, children: [value] };
}

export function box(props: BoxProps, children: Node[]): Node {
  return { type: "Box", props, children };
}

export function button(key: string, hotkey: string, label: string): Node {
  return { type: "Button", props: { key, label, hotkey, plain: true } };
}

export type ElementTable = Record<string, (props: Record<string, unknown>) => unknown>;

/** What a press on each Button key runs. */
export type PressTable = Record<string, () => void>;

export function materialize(node: Node, table: ElementTable, presses: PressTable = {}): unknown {
  const build = table[node.type];
  if (!build) throw new Error(`element ${node.type} is not on this surface`);
  if (node.type === "Button") {
    const onPress = presses[node.props.key];
    if (!onPress) throw new Error(`button ${node.props.key} has no action`);
    return build({ ...node.props, onPress });
  }
  const children =
    node.type === "Text" ? node.children : node.children.map((child) => materialize(child, table, presses));
  return build({ ...node.props, children });
}
