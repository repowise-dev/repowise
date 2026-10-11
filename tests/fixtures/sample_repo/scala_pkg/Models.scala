package sample.models

/** Supported operations. */
sealed trait Operation

object Operation {
  case object Add extends Operation
  case object Subtract extends Operation
}

/** Record of a calculation. */
case class CalculationRecord(
  operation: Operation,
  x: Double,
  y: Double,
  result: Double
)
