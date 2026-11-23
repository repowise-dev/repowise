import { readFileSync } from "node:fs";

import { describe, expect, it } from "vitest";

import { CARD_BASIS_KINDS, CARD_REF_TYPES } from "./cards";
import { BASIS_SPEC, MAX_WIRE_NUMBER, REF_SPEC, type Spec } from "./validation";
import {
  CHART_SORT_ORDERS,
  CHART_TYPES,
  VIEW_REGISTRY,
  VIEW_COVERAGE_METRICS,
  VIEW_KINDS,
  VIEW_LIMITS,
  VIEW_SCHEMA_VERSION,
  VIEW_SCHEMA_VERSIONS,
  parseViewParts,
  validateViewPart,
  validateViewParts,
  type ViewPart,
} from "./views";
import { COVERAGE_SPEC, SOURCE_SPEC } from "./views/base";
import { CHART_SPEC } from "./views/chart";
import { NARRATIVE_SPEC, SECTION_SPEC } from "./views/section";
import { COLUMN_SPEC, TABLE_DATA_SPEC, TABLE_SPEC } from "./views/table";

type Json = Record<string, unknown>;
interface Derived {
  from: string;
  drop?: string[];
  set?: Json;
  grow?: Record<string, number>;
}
interface Fixture {
  vocabulary: Json;
  limits: Json;
  fields: Record<string, string[]>;
  valid: Record<string, ViewPart>;
  invalid: Array<Derived & { name: string; error: string }>;
  valid_variants: Array<Derived & { name: string }>;
  valid_lists: Array<{ name: string; items: Derived[] }>;
  invalid_lists: Array<{ name: string; items: Derived[]; repeat?: number; error: string }>;
}

const fixture = JSON.parse(
  readFileSync(new URL("../../types/fixtures/views.conformance.json", import.meta.url), "utf-8"),
) as Fixture;

/** The parent of a dotted path's last segment, and that segment. */
function locate(root: unknown, path: string): [Json, string] {
  const parts = path.split(".");
  const last = parts.pop()!;
  const parent = parts.reduce<unknown>((node, key) => (node as Json)[key], root);
  return [parent as Json, last];
}

function derive({ from, drop = [], set = {}, grow = {} }: Derived): unknown {
  const part = structuredClone(fixture.valid[from]) as unknown;
  for (const path of drop) {
    const [parent, key] = locate(part, path);
    delete parent[key];
  }
  for (const [path, value] of Object.entries(set)) {
    const [parent, key] = locate(part, path);
    parent[key] = structuredClone(value);
  }
  for (const [path, length] of Object.entries(grow)) {
    const [parent, key] = locate(part, path);
    const items = parent[key] as unknown[];
    parent[key] = Array.from({ length }, () => structuredClone(items[0]));
  }
  return part;
}

const SPECS: Record<string, Spec<never>> = {
  CardBasis: BASIS_SPEC,
  CardRef: REF_SPEC,
  ViewSource: SOURCE_SPEC,
  ViewCoverage: COVERAGE_SPEC,
  ViewColumn: COLUMN_SPEC,
  ViewTableData: TABLE_DATA_SPEC,
  NarrativeBlock: NARRATIVE_SPEC,
  TableView: TABLE_SPEC,
  ChartView: CHART_SPEC,
  SectionView: SECTION_SPEC,
} as unknown as Record<string, Spec<never>>;

describe("view part contract shared with the server", () => {
  it("uses the shared vocabulary and limits", () => {
    expect({
      schema_version: VIEW_SCHEMA_VERSION,
      schema_versions: VIEW_SCHEMA_VERSIONS,
      max_wire_number: MAX_WIRE_NUMBER,
      view_kinds: VIEW_KINDS,
      chart_types: CHART_TYPES,
      coverage_metrics: VIEW_COVERAGE_METRICS,
      sort_orders: CHART_SORT_ORDERS,
      basis_kinds: CARD_BASIS_KINDS,
      ref_types: CARD_REF_TYPES,
    }).toEqual(fixture.vocabulary);
    expect(VIEW_LIMITS).toEqual(fixture.limits);
  });

  it.each(Object.entries(fixture.fields))("checks exactly the shared fields of %s", (name, fields) => {
    const spec = SPECS[name]!;
    expect(Object.keys({ ...spec.required, ...spec.optional }).sort()).toEqual([...fields].sort());
  });

  it("has a valid fixture for every kind", () => {
    const kinds = new Set(Object.values(fixture.valid).map((v) => v.kind));
    expect([...kinds].sort()).toEqual([...VIEW_KINDS].sort());
  });

  it.each(Object.entries(fixture.valid))("accepts %s", (_id, view) => {
    expect(validateViewPart(view)).toEqual({ ok: true, view });
  });

  it.each(Object.entries(fixture.valid))("round-trips %s through JSON unchanged", (_id, view) => {
    const wire = JSON.parse(JSON.stringify(view)) as unknown;
    expect(wire).toEqual(view);
    expect(validateViewPart(wire)).toEqual({ ok: true, view });
  });

  it.each(fixture.valid_variants.map((c) => [c.name, c] as const))("accepts the variant: %s", (_name, c) => {
    const view = derive(c);
    expect(validateViewPart(view)).toEqual({ ok: true, view });
  });

  it.each(fixture.invalid.map((c) => [c.name, c] as const))("rejects %s", (_name, c) => {
    const result = validateViewPart(derive(c));
    expect(result.ok).toBe(false);
    if (!result.ok) expect(result.errors.join("\n")).toContain(c.error);
  });

  it.each(fixture.valid_lists.map((c) => [c.name, c] as const))("accepts the list: %s", (_name, c) => {
    const views = c.items.map(derive);
    expect(validateViewParts(views)).toEqual({ ok: true, views });
  });

  it.each(fixture.invalid_lists.map((c) => [c.name, c] as const))("rejects the list: %s", (_name, c) => {
    const items = c.items.map(derive);
    const views = c.repeat ? Array.from({ length: c.repeat }, () => items[0]) : items;
    const result = validateViewParts(views);
    expect(result.ok).toBe(false);
    if (!result.ok) expect(result.errors.join("\n")).toContain(c.error);
  });
});

describe("view part validator", () => {
  const table = fixture.valid.t1!;

  it.each([
    [
      "NaN cell",
      { ...table, rows: [{ path: "a.py", churn: Number.NaN, health: 1 }], total_rows: 1, truncated: false },
    ],
    ["infinite coverage", { ...table, coverage: { metric: "test_reach", ratio: Number.POSITIVE_INFINITY } }],
    ["optional key set to undefined", { ...table, summary: undefined }],
    ["inherited name as a field", { ...table, constructor: "x" }],
    [
      "required field only on the prototype",
      Object.assign(Object.create({ title: "From the prototype" }), (({ title: _t, ...rest }) => rest)(table)),
    ],
    ["own __proto__", JSON.parse(`{"__proto__": {"x": 1}, ${JSON.stringify(table).slice(1)}`)],
  ])("rejects %s", (_name, view) => {
    expect(validateViewPart(view).ok).toBe(false);
  });

  it("needs a registry entry for every view kind", () => {
    // @ts-expect-error a kind without an entry fails to compile
    const missing: typeof VIEW_REGISTRY = { table: VIEW_REGISTRY.table, chart: VIEW_REGISTRY.chart };
    // @ts-expect-error isData is typed from the part, so a data kind cannot say false
    const wrong: typeof VIEW_REGISTRY.table = { ...VIEW_REGISTRY.table, isData: false };
    expect([missing, wrong]).toHaveLength(2);
  });

  it("marks exactly the kinds with provenance as data", () => {
    const data = Object.entries(VIEW_REGISTRY).filter(([, entry]) => entry.isData);
    expect(data.map(([kind]) => kind)).toEqual(["table", "chart"]);
  });

  it("keeps what a renderer can show from an untrusted list", () => {
    const { t1, c1, c2, s1 } = fixture.valid as Record<string, ViewPart>;
    const dangling = { ...s1!, id: "s2", view_ids: ["missing"] };
    const parsed = parseViewParts([t1, { kind: "pie" }, c1, { ...c2!, id: "t1" }, s1, dangling]);
    expect(parsed.map((v) => v.id)).toEqual(["t1", "c1", "s1"]);
    expect(parseViewParts(undefined)).toEqual([]);
    expect(parseViewParts(Array.from({ length: VIEW_LIMITS.parts + 5 }, (_, i) => ({ ...t1!, id: `t${i}` })))).toHaveLength(
      VIEW_LIMITS.parts,
    );
  });

  it("keeps a chart it cannot draw as its data table", () => {
    const { c1 } = fixture.valid as Record<string, ViewPart>;
    const newer = { ...c1!, schema_version: 3, chart_type: "sankey", flows: [] };
    const [kept] = parseViewParts([newer]);
    expect(kept?.kind).toBe("table");
    expect(kept && "rows" in kept ? kept.rows : null).toEqual((c1 as { data: { rows: unknown } }).data.rows);
    expect(kept?.schema_version).toBe(VIEW_SCHEMA_VERSION);
    // A table with a field this client does not know keeps its known fields.
    const { t1 } = fixture.valid as Record<string, ViewPart>;
    expect(parseViewParts([{ ...t1!, sparkline: [1, 2] }])[0]?.kind).toBe("table");
    // Something that is no table at all is still dropped.
    expect(parseViewParts([{ ...c1!, chart_type: "sankey", data: { rows: "x" } }])).toEqual([]);
  });

  it("reads parts stored at schema version 1", () => {
    const { c1 } = fixture.valid as Record<string, ViewPart>;
    expect(c1?.schema_version).toBe(1);
    expect(validateViewPart(c1).ok).toBe(true);
    expect(validateViewPart({ ...c1!, schema_version: 2 }).ok).toBe(true);
  });
});
