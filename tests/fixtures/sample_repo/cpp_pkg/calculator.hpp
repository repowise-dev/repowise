#pragma once

#include <stdexcept>
#include <vector>
#include "models.hpp"

namespace sample {

/**
 * @brief Stateful calculator with history recording.
 *
 * Performs the four basic arithmetic operations and maintains a list of
 * CalculationRecord entries for audit purposes.
 */
class Calculator {
public:
    Calculator() = default;

    /** Adds x and y, records and returns the result. */
    double add(double x, double y);

    /** Subtracts y from x, records and returns the result. */
    double subtract(double x, double y);

    /** Multiplies x by y, records and returns the result. */
    double multiply(double x, double y);

    /**
     * @brief Divides x by y.
     * @throws std::invalid_argument if y is zero.
     */
    double divide(double x, double y);

    /** Returns the history of all calculations. */
    const std::vector<CalculationRecord>& history() const;

    /** Clears all recorded calculations. */
    void clear_history();

private:
    std::vector<CalculationRecord> history_;

    void record(Operation op, double x, double y, double result);
};

}  // namespace sample
