#pragma once

#include <string>

namespace sample {

/** Arithmetic operation type. */
enum class Operation {
    Add,
    Subtract,
    Multiply,
    Divide,
};

/** Returns the display name of an operation. */
inline std::string operation_name(Operation op) {
    switch (op) {
        case Operation::Add:      return "add";
        case Operation::Subtract: return "subtract";
        case Operation::Multiply: return "multiply";
        case Operation::Divide:   return "divide";
    }
    return "unknown";
}

/** An immutable record of a single arithmetic calculation. */
struct CalculationRecord {
    Operation operation;
    double x;
    double y;
    double result;

    /** Returns a human-readable summary. */
    std::string summary() const;
};

}  // namespace sample
