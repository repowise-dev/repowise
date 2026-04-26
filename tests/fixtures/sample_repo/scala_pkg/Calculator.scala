package sample

import sample.models.Operation
import sample.models.CalculationRecord

/** Calculator for basic arithmetic. */
class Calculator extends BaseCalc with Computable {
  def add(x: Double, y: Double): Double = x + y

  def subtract(x: Double, y: Double): Double = x - y

  private def helper(): Unit = {}

  def compute(): Double = 0.0
}

trait Computable {
  def compute(): Double
}
