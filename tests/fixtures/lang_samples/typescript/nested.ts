export function deeplyNested(x: number): number {
  if (x > 0) {
    for (let i = 0; i < x; i++) {
      if (i % 2 === 0) {
        while (i > 0) {
          try {
            if (i === 5) {
              return i;
            }
          } catch (e) {
            // noop
          }
          i--;
        }
      }
    }
  }
  return 0;
}

export function shallow(x: number): number {
  return x + 1;
}
