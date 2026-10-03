/**
 * Views build plain trees with these builders, so they stay pure and testable
 * without a surface. register.ts turns a tree into real elements with
 * `materialize` over the table `$.ui.resolve(e)` returns.
 */

export interface TextProps {
  dimColor?: boolean;
  wrap?: "truncate-end";
  /** A theme key (`success`, `warning`, `error`: the terminal's own colors for health) or a hex color. */
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
  /** Dim at rest, full strength under the focus: a tab not shown. */
  dimColor?: boolean;
}

/** A one-line field; its submit is read by register.ts's `ui.input` hook, not a closure. */
export interface InputProps {
  key: string;
  label: string;
  placeholder: string;
  submitLabel: string;
  value?: string;
  autoFocus?: true;
}

export interface MarkdownProps {
  /** At most 10,000 characters. */
  text: string;
}

/** A drawing for the surfaces that draw SVG (the desktop app); at most 131,072 characters. */
export interface SvgProps {
  source: string;
  alt: string;
}

/** A terminal cell grid; `cells` packs `columns * rows` cells (views/map.ts). */
export interface RasterProps {
  key: string;
  columns: number;
  rows: number;
  cells: string;
}

export type Node =
  | { type: "Text"; props: TextProps; children: string[] }
  | { type: "Box"; props: BoxProps; children: Node[] }
  | { type: "Button"; props: ButtonProps }
  | { type: "Raster"; props: RasterProps }
  | { type: "Input"; props: InputProps }
  | { type: "Markdown"; props: MarkdownProps }
  | { type: "Svg"; props: SvgProps };

export function text(value: string, props: TextProps = {}): Node {
  return { type: "Text", props, children: [value] };
}

export function box(props: BoxProps, children: Node[]): Node {
  return { type: "Box", props, children };
}

export function button(key: string, hotkey: string, label: string, dimColor = false): Node {
  return { type: "Button", props: { key, label, hotkey, plain: true, ...(dimColor ? { dimColor } : {}) } };
}

export function raster(props: RasterProps): Node {
  return { type: "Raster", props };
}

export function input(props: InputProps): Node {
  return { type: "Input", props };
}

export function markdown(text: string): Node {
  return { type: "Markdown", props: { text } };
}

export function svg(source: string, alt: string): Node {
  return { type: "Svg", props: { source, alt } };
}

/** The Input's own closure: the submit is handled in the `ui.input` hook, where a lookup may start. */
const SUBMIT_IN_HOOK = (): void => undefined;

export type ElementTable = Record<string, (props: Record<string, unknown>) => unknown>;

/** What a press on each Button key runs. */
export type PressTable = Record<string, () => void>;

type Props = Record<string, unknown>;
type PropsOf = { [K in Node["type"]]: (node: Extract<Node, { type: K }>, table: ElementTable, presses: PressTable) => Props };

function pressFor(key: string, presses: PressTable): () => void {
  const onPress = presses[key];
  if (!onPress) throw new Error(`button ${key} has no action`);
  return onPress;
}

// Leaves (Raster, Markdown, Svg) pass their props alone: the element refuses a `children` prop.
const PROPS: PropsOf = {
  Text: (n) => ({ ...n.props, children: n.children }),
  Box: (n, table, presses) => ({ ...n.props, children: n.children.map((child) => materialize(child, table, presses)) }),
  Button: (n, _table, presses) => ({ ...n.props, onPress: pressFor(n.props.key, presses) }),
  Input: (n) => ({ ...n.props, onSubmit: SUBMIT_IN_HOOK }),
  Raster: (n) => ({ ...n.props }),
  Markdown: (n) => ({ ...n.props }),
  Svg: (n) => ({ ...n.props }),
};

export function materialize(node: Node, table: ElementTable, presses: PressTable = {}): unknown {
  const build = table[node.type];
  if (!build) throw new Error(`element ${node.type} is not on this surface`);
  return build((PROPS[node.type] as (n: Node, t: ElementTable, p: PressTable) => Props)(node, table, presses));
}
