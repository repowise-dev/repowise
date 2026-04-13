import Foundation

/// Calculator for basic arithmetic.
class Calculator: Computable {
    func add(x: Double, y: Double) -> Double {
        return x + y
    }

    func subtract(x: Double, y: Double) -> Double {
        return x - y
    }

    private func helper() {}

    func compute() -> Double {
        return 0.0
    }
}
