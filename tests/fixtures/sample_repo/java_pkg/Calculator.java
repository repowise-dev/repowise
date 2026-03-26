package com.repowise.sample;

import java.util.ArrayList;
import java.util.List;

/**
 * Stateful calculator that performs arithmetic operations and records history.
 *
 * <p>Wraps four basic operations (add, subtract, multiply, divide) and maintains
 * a list of {@link CalculationRecord} entries for audit purposes.
 */
public class Calculator {

    private final List<CalculationRecord> history = new ArrayList<>();

    /**
     * Adds {@code x} and {@code y}.
     *
     * @param x first operand
     * @param y second operand
     * @return the sum
     */
    public double add(double x, double y) {
        double result = x + y;
        record(Operation.ADD, x, y, result);
        return result;
    }

    /**
     * Subtracts {@code y} from {@code x}.
     *
     * @param x minuend
     * @param y subtrahend
     * @return the difference
     */
    public double subtract(double x, double y) {
        double result = x - y;
        record(Operation.SUBTRACT, x, y, result);
        return result;
    }

    /** Returns the product of {@code x} and {@code y}. */
    public double multiply(double x, double y) {
        double result = x * y;
        record(Operation.MULTIPLY, x, y, result);
        return result;
    }

    /**
     * Divides {@code x} by {@code y}.
     *
     * @throws ArithmeticException if {@code y} is zero
     */
    public double divide(double x, double y) {
        if (y == 0) {
            throw new ArithmeticException("Division by zero");
        }
        double result = x / y;
        record(Operation.DIVIDE, x, y, result);
        return result;
    }

    /** Returns an unmodifiable view of the calculation history. */
    public List<CalculationRecord> getHistory() {
        return List.copyOf(history);
    }

    /** Clears all recorded calculations. */
    public void clearHistory() {
        history.clear();
    }

    private void record(Operation op, double x, double y, double result) {
        history.add(new CalculationRecord(op, x, y, result));
    }
}
