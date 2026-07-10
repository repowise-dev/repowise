// Perf-dialect fixture: sinks in loops, regex recompiles, string concat,
// sync-over-Future, lock contention - plus the negatives that must stay quiet
// (hoisted regex, constant-bound loop, plain helper calls, collection `+=`).

package fixtures

import java.util.regex.Pattern

import scala.concurrent.{Await, Future}
import scala.concurrent.duration.Duration
import scala.io.Source

object PerfIoInLoop {
  def readAll(paths: List[String]): List[String] = {
    var out = List.empty[String]
    for (p <- paths) {
      val src = Source.fromFile(p) // io_in_loop (filesystem)
      out = out :+ src.mkString
    }
    out
  }

  def matrix(rows: List[List[String]]): Unit = {
    for (row <- rows) {
      for (cell <- row) {
        Source.fromFile(cell) // io_in_loop + nested_loop_with_io
      }
    }
  }

  def regexInLoop(lines: List[String]): Int = {
    var n = 0
    for (line <- lines) {
      val re = "a+b".r // regex_compile_in_loop (StringOps.r)
      val p = Pattern.compile("x+y") // regex_compile_in_loop (JVM interop)
      if (re.findFirstIn(line).isDefined) n += 1
    }
    n
  }

  def hoistedRegex(lines: List[String]): Int = {
    val re = "a+b".r // hoisted - no hit
    var n = 0
    for (line <- lines) {
      if (re.findFirstIn(line).isDefined) n += 1
    }
    n
  }

  def concat(items: List[String]): String = {
    var acc = ""
    var i = 0
    while (i < items.length) {
      acc += "chunk" // string_concat_in_loop (var String binding)
      i += 1
    }
    acc
  }

  def bufferAppend(items: List[String]): Unit = {
    val buf = scala.collection.mutable.ListBuffer.empty[String]
    for (item <- items) {
      buf += "chunk" // NOT flagged - `buf` has no `var = "<string>"` binding
    }
  }

  def slow(fut: Future[Int]): Future[Int] = {
    val x = Await.result(fut, Duration.Inf) // blocking_sync_in_async
    Future.successful(x + 1)
  }

  def constantBound(): Int = {
    var n = 0
    for (i <- 1 to 3) {
      Source.fromFile("f.txt") // constant-bound loop - not data-dependent
      n += 1
    }
    n
  }

  def locked(items: List[String], lock: AnyRef): Unit = {
    for (item <- items) {
      lock.synchronized { // lock_in_loop
        process(item)
      }
    }
  }

  def helperInLoop(items: List[String]): Unit = {
    for (item <- items) {
      process(item) // a plain helper call is not a sink
    }
  }

  private def process(s: String): Unit = ()
}
