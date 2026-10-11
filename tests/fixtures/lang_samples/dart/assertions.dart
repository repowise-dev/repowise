// Assertion-block fixtures — Dart's bare ``assert(...)`` statement.
// package:test ``expect(...)`` calls have no call-expression node type to
// key on, so only assert statements count (documented under-signal).

void testManyAsserts(int x) {
  assert(x > 0);
  assert(x < 100);
  assert(x != 13);
  assert(x != 42);
  assert(x % 2 == 0);
}

void testFewAsserts(int x) {
  assert(x > 0);
  var y = x + 1;
  assert(y > 1);
}
