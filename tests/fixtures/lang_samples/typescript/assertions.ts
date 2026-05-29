// Fixture for assertion-block detection (test-quality smells).

function testManyExpects(): void {
  const x = 1;
  expect(x).toBe(1);
  expect(x).toBe(1);
  expect(x).toBe(1);
  expect(x).toBe(1);
  expect(x).toBe(1);
  expect(x).toBe(1);
  expect(x).toBe(1);
  expect(x).toBe(1);
  expect(x).toBe(1);
  expect(x).toBe(1);
  expect(x).toBe(1);
  expect(x).toBe(1);
  expect(x).toBe(1);
  expect(x).toBe(1);
  expect(x).toBe(1);
  expect(x).toBe(1);
}

function testFewExpects(): void {
  expect(1).toBe(1);
  const y = 2;
  expect(y).toBe(2);
}
