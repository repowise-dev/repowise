/** The `table` view kind, and the table data a chart plots. */

import {
  VIEW_LIMITS,
  type TableView,
  type ViewColumn,
  type ViewTableData,
} from "@repowise-dev/types/views";

import {
  basis,
  bool,
  count,
  duplicates,
  isFiniteNumber,
  isRec,
  list,
  matches,
  obj,
  ref,
  str,
  type Check,
  type Rec,
  type Spec,
} from "../validation";
import { PROVENANCE_SPEC, baseSpec, type ViewKindEntry } from "./base";

export const COLUMN_SPEC: Spec<ViewColumn> = {
  required: { key: matches(/^[a-z][a-z0-9_]{0,63}$/, "a lowercase column key"), label: str },
  optional: { unit: str, basis },
};

const cell: Check = (v, p, e) => {
  if (v === null || typeof v === "string" || isFiniteNumber(v)) return;
  if (isRec(v)) ref(v, p, e);
  else e.push(`${p}: expected a string, a finite number, null or a ref`);
};

const row: Check = (v, p, e) => {
  if (!isRec(v)) {
    e.push(`${p}: expected an object`);
    return;
  }
  for (const key of Object.keys(v)) cell(v[key], `${p}.${key}`, e);
};

/** Column keys of a table data object, or null when its columns are unreadable. */
export function columnKeys(data: unknown): string[] | null {
  if (!isRec(data) || !Array.isArray(data.columns)) return null;
  return data.columns.map((c) => (isRec(c) ? c.key : undefined)).filter((k): k is string => typeof k === "string");
}

export const TABLE_DATA_SPEC: Spec<ViewTableData> = {
  required: {
    columns: list(obj(COLUMN_SPEC), VIEW_LIMITS.columns),
    rows: list(row, VIEW_LIMITS.rows),
    total_rows: count,
    truncated: bool,
  },
  optional: {},
};

/** Rules across the fields of table data: row keys, totals, truncation. */
export function tableDataRules(v: Rec, p: string, e: string[]): void {
  const keys = columnKeys(v) ?? [];
  if (Array.isArray(v.columns) && v.columns.length === 0) e.push(`${p}.columns: at least one column`);
  for (const key of duplicates(keys)) e.push(`${p}.columns: duplicate key ${String(key)}`);
  if (!Array.isArray(v.rows)) return;
  const known = new Set(keys);
  v.rows.forEach((r, i) => {
    if (!isRec(r)) return;
    for (const key of known) if (!Object.hasOwn(r, key)) e.push(`${p}.rows[${i}].${key}: required`);
    for (const key of Object.keys(r)) {
      if (!known.has(key)) e.push(`${p}.rows[${i}].${key}: unknown column`);
    }
  });
  if (Number.isInteger(v.total_rows) && typeof v.truncated === "boolean") {
    const total = v.total_rows as number;
    if (total < v.rows.length) e.push(`${p}.total_rows: fewer than the rows given`);
    else if (v.truncated !== total > v.rows.length) {
      e.push(`${p}.truncated: must be true exactly when total_rows exceeds the rows given`);
    }
  }
}

export const tableData = obj(TABLE_DATA_SPEC, tableDataRules);

const base = baseSpec("table");
export const TABLE_SPEC: Spec<TableView> = {
  required: { ...base.required, ...PROVENANCE_SPEC.required, ...TABLE_DATA_SPEC.required },
  optional: { ...base.optional, ...PROVENANCE_SPEC.optional },
};

export const TABLE_KIND: ViewKindEntry<"table"> = {
  check: obj(TABLE_SPEC, tableDataRules),
  isData: true,
};
