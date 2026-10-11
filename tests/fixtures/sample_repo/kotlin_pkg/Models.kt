package sample.models

/** Supported arithmetic operations. */
enum class Operation {
    ADD,
    SUBTRACT,
    MULTIPLY,
    DIVIDE
}

/** Record of a single calculation. */
data class CalculationRecord(
    val operation: Operation,
    val x: Double,
    val y: Double,
    val result: Double
)
