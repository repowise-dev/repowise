'use strict';

class Matrix {
  constructor(n) {
    this.n = n;
    this.cells = new Float64Array(n * n);
    for (let i = 0; i < n * n; i++) this.cells[i] = (i % 7) + 1;
  }

  multiply(other) {
    const n = this.n;
    const out = new Matrix(n);
    for (let i = 0; i < n; i++) {
      for (let j = 0; j < n; j++) {
        let sum = 0;
        for (let k = 0; k < n; k++) sum += this.cells[i * n + k] * other.cells[k * n + j];
        out.cells[i * n + j] = sum;
      }
    }
    return out;
  }

  trace() {
    let t = 0;
    for (let i = 0; i < this.n; i++) t += this.cells[i * this.n + i];
    return t;
  }
}

const stats = {
  mean(values) {
    let s = 0;
    for (const v of values) s += v;
    return s / values.length;
  },
};

module.exports = { Matrix, stats };
