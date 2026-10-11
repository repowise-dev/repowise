// Fixtures for control-flow walker tests (nesting, CCN, guards, match).

object Nested {
  def deeplyNested(items: List[Int]): Int = {
    var total = 0
    for (a <- items) {
      if (a > 0) {
        for (b <- items) {
          if (b > 1) {
            while (total < 10) {
              total += 1
            }
          }
        }
      }
    }
    total
  }

  def manyBranches(a: Int, b: Int): Int = {
    if (a == 1) { return 1 }
    else if (a == 2) { return 2 }
    else if (a == 3) { return 3 }
    else if (a == 4) { return 4 }
    else if (a == 5) { return 5 }
    if (a > 10 && b > 0 || b < -5) { return 6 }
    0
  }

  def matchGuards(n: Int): String = {
    n match {
      case 0 => "zero"
      case x if x > 100 => "big"
      case x if x < -100 => "small"
      case _ => "mid"
    }
  }

  def flatMatch(n: Int): String = {
    n match {
      case 0 => "zero"
      case 1 => "one"
      case _ => "other"
    }
  }

  def forGuard(items: List[Int]): Int = {
    var n = 0
    for (i <- items if i > 0) {
      n += i
    }
    n
  }

  def shallow(x: Int): Int = x + 1
}
