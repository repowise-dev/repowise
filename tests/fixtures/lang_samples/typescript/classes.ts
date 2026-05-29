// Fixtures for class-level (LCOM4) walker tests.

class Cohesive {
  total = 0;
  count = 0;

  add(n: number): void {
    this.total += n;
    this.count += 1;
  }

  average(): number {
    return this.count ? this.total / this.count : 0;
  }

  reset(): void {
    this.total = 0;
    this.count = 0;
  }

  describe(): string {
    return `${this.count} items, avg ${this.average()}`;
  }
}

// Fields are declared (not assigned in a constructor) so no single
// method bridges the two clusters → LCOM4 == 3.
class Splintered {
  a = 0;
  b = 0;

  setA(v: number): void {
    this.a = v;
  }

  getA(): number {
    return this.a;
  }

  setB(v: number): void {
    this.b = v;
  }

  getB(): number {
    return this.b;
  }

  loner(): number {
    return 42;
  }
}
