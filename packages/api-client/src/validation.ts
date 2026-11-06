/**
 * Dependency-free validation primitives shared by the evidence card and view
 * part validators.
 *
 * Objects are checked strictly: unknown fields are rejected, so a payload can
 * never carry data its schema does not name (a person field, a renderer hint)
 * and a valid payload round-trips through JSON unchanged. Only own keys count,
 * so a prototype can neither supply a required field nor smuggle in an
 * inherited one. A check reports every problem it reaches; a value of the
 * wrong shape (not an object, a list over its cap) stops the checks beneath it.
 *
 * Numbers are finite and within +/-MAX_WIRE_NUMBER, so every accepted number
 * survives a JSON round trip in both TypeScript and Python unchanged.
 */

import {
  CARD_BASIS_KINDS,
  CARD_REF_TYPES,
  type CardBasis,
  type CardRef,
} from "@repowise-dev/types/cards";

export type Rec = Record<string, unknown>;
export type Check = (value: unknown, path: string, errors: string[]) => void;

type OptionalKeys<T> = { [K in keyof T]-?: object extends Pick<T, K> ? K : never }[keyof T];
type RequiredKeys<T> = Exclude<keyof T, OptionalKeys<T>>;
/**
 * One check per field of `T`, split by whether `T` requires it. A field added
 * to a type without a check here fails to compile.
 */
export interface Spec<T> {
  required: { [K in RequiredKeys<T>]: Check };
  optional: { [K in OptionalKeys<T>]: Check };
}

export const isRec = (v: unknown): v is Rec =>
  typeof v === "object" && v !== null && !Array.isArray(v);

export const str: Check = (v, p, e) => {
  if (typeof v !== "string" || v.length === 0) e.push(`${p}: expected a non-empty string`);
};
export const bool: Check = (v, p, e) => {
  if (typeof v !== "boolean") e.push(`${p}: expected a boolean`);
};
/** 2^53 - 1: the largest integer both JSON readers keep exact. */
export const MAX_WIRE_NUMBER = Number.MAX_SAFE_INTEGER;
/** A finite number within +/-MAX_WIRE_NUMBER. */
export const isFiniteNumber = (v: unknown): v is number =>
  typeof v === "number" && Number.isFinite(v) && Math.abs(v) <= MAX_WIRE_NUMBER;
const isWireInteger = (v: unknown): v is number => Number.isInteger(v) && isFiniteNumber(v);
export const line: Check = (v, p, e) => {
  if (!isWireInteger(v) || v < 1) e.push(`${p}: expected a positive integer`);
};
export const oneOf =
  (values: readonly string[]): Check =>
  (v, p, e) => {
    if (typeof v !== "string" || !values.includes(v)) {
      e.push(`${p}: expected one of ${values.join(", ")}`);
    }
  };
export const matches =
  (pattern: RegExp, what: string): Check =>
  (v, p, e) => {
    if (typeof v !== "string" || !pattern.test(v)) e.push(`${p}: expected ${what}`);
  };
export const oneOfNumbers =
  (values: readonly number[]): Check =>
  (v, p, e) => {
    if (typeof v !== "number" || !values.includes(v)) {
      e.push(`${p}: expected one of ${values.join(", ")}`);
    }
  };
export const constant =
  (expected: string | number): Check =>
  (v, p, e) => {
    if (v !== expected) e.push(`${p}: expected ${expected}`);
  };
export const nullable =
  (check: Check): Check =>
  (v, p, e) => {
    if (v !== null) check(v, p, e);
  };
export const count: Check = (v, p, e) => {
  if (!isWireInteger(v) || v < 0) e.push(`${p}: expected a non-negative integer`);
};
export const ratio: Check = (v, p, e) => {
  if (!isFiniteNumber(v) || v < 0 || v > 1) e.push(`${p}: expected a number from 0 to 1`);
};
export const value: Check = (v, p, e) => {
  if (!(typeof v === "string" || isFiniteNumber(v))) {
    e.push(`${p}: expected a string or a finite number`);
  }
};
const ISO_8601 = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})(?::(\d{2})(?:\.\d+)?)?(?:Z|[+-]\d{2}:\d{2})$/;
/** Date.parse rolls impossible dates over (Feb 30 becomes Mar 2), so the fields are compared back. */
function isIsoTimestamp(v: string): boolean {
  const m = ISO_8601.exec(v);
  if (!m) return false;
  const [y, mo, d, h, mi, sec] = m.slice(1, 7).map((x) => Number(x ?? 0));
  const t = new Date(Date.UTC(y!, mo! - 1, d!, h!, mi!, sec!));
  return (
    t.getUTCFullYear() === y &&
    t.getUTCMonth() === mo! - 1 &&
    t.getUTCDate() === d &&
    t.getUTCHours() === h &&
    t.getUTCMinutes() === mi &&
    t.getUTCSeconds() === sec
  );
}
export const timestamp: Check = (v, p, e) => {
  if (typeof v !== "string" || !isIsoTimestamp(v)) {
    e.push(`${p}: expected an ISO 8601 timestamp with a time zone`);
  }
};

/** Checks an object's own fields; any other own key is an error. */
export function obj<T>(spec: Spec<T>, refine?: (v: Rec, p: string, e: string[]) => void): Check {
  const required = Object.entries(spec.required) as Array<[string, Check]>;
  const optional = Object.entries(spec.optional) as Array<[string, Check]>;
  const known = new Set([...required, ...optional].map(([key]) => key));
  return (v, p, e) => {
    if (!isRec(v)) {
      e.push(`${p}: expected an object`);
      return;
    }
    for (const [key, check] of required) {
      if (!Object.hasOwn(v, key)) e.push(`${p}.${key}: required`);
      else check(v[key], `${p}.${key}`, e);
    }
    for (const [key, check] of optional) {
      // Present means checked, so an optional key set to undefined is rejected.
      if (Object.hasOwn(v, key)) check(v[key], `${p}.${key}`, e);
    }
    for (const key of Object.keys(v)) {
      if (!known.has(key)) e.push(`${p}.${key}: unknown field`);
    }
    refine?.(v, p, e);
  };
}

/** An array of at most `max` items, each checked. */
export const list =
  (item: Check, max: number): Check =>
  (v, p, e) => {
    if (!Array.isArray(v)) {
      e.push(`${p}: expected an array`);
      return;
    }
    if (v.length > max) {
      e.push(`${p}: more than ${max} items`);
      return;
    }
    v.forEach((x, i) => item(x, `${p}[${i}]`, e));
  };

export const none = {} as const;

/** Values listed more than once, each reported once. */
export function duplicates<T>(items: readonly T[]): T[] {
  const seen = new Set<T>();
  const repeated = new Set<T>();
  for (const item of items) (seen.has(item) ? repeated : seen).add(item);
  return [...repeated];
}

export const BASIS_SPEC: Spec<CardBasis> = {
  required: { kind: oneOf(CARD_BASIS_KINDS), source: str },
  optional: none,
};
export const basis = obj(BASIS_SPEC);

export const REF_SPEC: Spec<CardRef> = {
  required: { type: oneOf(CARD_REF_TYPES), id: str },
  optional: { start_line: line, end_line: line },
};
export const ref = obj(REF_SPEC, (r, p, e) => {
  const hasStart = Object.hasOwn(r, "start_line");
  const hasEnd = Object.hasOwn(r, "end_line");
  if ((hasStart || hasEnd) && r.type !== "file" && r.type !== "symbol") {
    e.push(`${p}: line numbers only apply to file and symbol refs`);
  }
  if (hasEnd && !hasStart) e.push(`${p}.end_line: requires start_line`);
  if (hasStart && hasEnd && (r.end_line as number) < (r.start_line as number)) {
    e.push(`${p}.end_line: before start_line`);
  }
});
