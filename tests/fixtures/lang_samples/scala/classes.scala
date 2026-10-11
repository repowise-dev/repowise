// Fixtures for class-level (LCOM4 / god-class) walker tests.
//
// Scala accesses instance members WITHOUT a receiver idiomatically; these
// fixtures use an explicit `this.` so the member-access node type is
// exercised. The implicit-receiver case is the documented "no signal" path.

class Cohesive {
  var total = 0
  var count = 0

  def add(n: Int): Unit = {
    this.total += n
    this.count += 1
  }

  def average(): Int = {
    if (this.count != 0) this.total / this.count else 0
  }

  def reset(): Unit = {
    this.total = 0
    this.count = 0
  }

  def describe(): Int = {
    this.count
  }
}

class Splintered {
  var a = 0
  var b = 0

  def setA(v: Int): Unit = {
    this.a = v
  }

  def getA(): Int = {
    this.a
  }

  def setB(v: Int): Unit = {
    this.b = v
  }

  def getB(): Int = {
    this.b
  }

  def loner(): Int = {
    42
  }
}

object Registry {
  def lookup(key: String): Int = {
    key.length
  }

  def register(key: String): String = {
    key.trim
  }
}

trait Shape {
  def area(): Double = {
    0.0
  }
}
