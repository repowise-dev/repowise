export function threeOps(a: boolean, b: boolean, c: boolean, d: boolean): number {
  if (a && b && c && d) {
    return 1;
  }
  return 0;
}

export function sixOps(
  a: boolean,
  b: boolean,
  c: boolean,
  d: boolean,
  e: boolean,
  f: boolean,
  g: boolean,
): number {
  while (a && b && c && d && e && f && g) {
    return 1;
  }
  return 0;
}

export function twoOps(a: boolean, b: boolean, c: boolean): number {
  if (a && b || c) {
    return 1;
  }
  return 0;
}
