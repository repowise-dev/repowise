#include "calculator.hpp"
#include <stdexcept>
#include <string>

namespace sample {

double Calculator::add(double x, double y) {
    double result = x + y;
    record(Operation::Add, x, y, result);
    return result;
}

double Calculator::subtract(double x, double y) {
    double result = x - y;
    record(Operation::Subtract, x, y, result);
    return result;
}

double Calculator::multiply(double x, double y) {
    double result = x * y;
    record(Operation::Multiply, x, y, result);
    return result;
}

double Calculator::divide(double x, double y) {
    if (y == 0.0) {
        throw std::invalid_argument("Division by zero");
    }
    double result = x / y;
    record(Operation::Divide, x, y, result);
    return result;
}

const std::vector<CalculationRecord>& Calculator::history() const {
    return history_;
}

void Calculator::clear_history() {
    history_.clear();
}

void Calculator::record(Operation op, double x, double y, double result) {
    history_.push_back(CalculationRecord{op, x, y, result});
}

}  // namespace sample
