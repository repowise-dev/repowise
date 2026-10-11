// Class-metric fixtures. Dart has no wrapper node for ``this.member``
// access (receiver and selector are flat siblings), so LCOM4 sits at its
// "no signal" safety valve (1) by design — the class facts under test here
// are method_count / total size / per-method complexity.

class Cohesive {
  int total = 0;
  int count = 0;

  void add(int v) {
    total += v;
    count += 1;
  }

  double mean() {
    return total / count;
  }

  void reset() {
    total = 0;
    count = 0;
  }
}

class Wide {
  int a = 0;
  int b = 0;

  void one() {
    a = 1;
  }

  void two() {
    b = 2;
  }

  void three() {
    a = 3;
  }

  void four() {
    b = 4;
  }

  int loner() {
    return 42;
  }
}
