// Fixture for assertion-block detection (test-quality smells).

fun testManyAsserts() {
    assertEquals(1, 1)
    assertEquals(1, 1)
    assertEquals(1, 1)
    assertEquals(1, 1)
    assertEquals(1, 1)
    assertEquals(1, 1)
    assertEquals(1, 1)
    assertEquals(1, 1)
    assertEquals(1, 1)
    assertEquals(1, 1)
    assertEquals(1, 1)
    assertEquals(1, 1)
    assertEquals(1, 1)
    assertEquals(1, 1)
    assertEquals(1, 1)
    assertEquals(1, 1)
}

fun testFewAsserts() {
    assertEquals(1, 1)
    val x = 2
    assertEquals(x, 2)
}
