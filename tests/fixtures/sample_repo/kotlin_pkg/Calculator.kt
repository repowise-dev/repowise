package sample

import sample.models.Operation
import sample.models.CalculationRecord

class Calculator {
    private val history = mutableListOf<CalculationRecord>()

    fun add(x: Double, y: Double): Double {
        val result = x + y
        record(Operation.ADD, x, y, result)
        return result
    }

    fun subtract(x: Double, y: Double): Double {
        val result = x - y
        record(Operation.SUBTRACT, x, y, result)
        return result
    }

    private fun record(op: Operation, x: Double, y: Double, result: Double) {
        history.add(CalculationRecord(op, x, y, result))
    }

    fun getHistory(): List<CalculationRecord> = history
}
