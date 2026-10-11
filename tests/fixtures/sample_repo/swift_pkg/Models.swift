import Foundation

protocol Computable {
    func compute() -> Double
}

enum Operation {
    case add
    case subtract
    case multiply
    case divide
}

struct CalculationRecord {
    var operation: Operation
    var x: Double
    var y: Double
    var result: Double
}
