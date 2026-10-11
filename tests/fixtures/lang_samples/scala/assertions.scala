// Fixtures for assertion-block detection (munit / plain-assert style calls).
//
// ScalaTest's infix DSL (`x shouldBe y`) has no assert-prefixed callee and is
// the documented detection gap - not exercised here.

class AssertionsSpec {
  def testManyAsserts(): Unit = {
    val x = compute()
    assert(x == 1)
    assert(x != 2)
    assertEquals(x, 1)
    assertNotEquals(x, 2)
    assert(x > 0)
  }

  def testFewAsserts(): Unit = {
    val x = compute()
    assert(x == 1)
    val y = x + 1
    assert(y == 2)
  }

  private def compute(): Int = 1

  private def assertEquals(a: Int, b: Int): Unit = ()

  private def assertNotEquals(a: Int, b: Int): Unit = ()
}
