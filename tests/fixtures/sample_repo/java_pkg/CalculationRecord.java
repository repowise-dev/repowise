package com.repowise.sample;

/**
 * An immutable record of a single arithmetic calculation.
 */
public final class CalculationRecord {

    private final Operation operation;
    private final double x;
    private final double y;
    private final double result;

    public CalculationRecord(Operation operation, double x, double y, double result) {
        this.operation = operation;
        this.x = x;
        this.y = y;
        this.result = result;
    }

    public Operation getOperation() { return operation; }
    public double getX()            { return x; }
    public double getY()            { return y; }
    public double getResult()       { return result; }

    /** Returns a human-readable summary of this record. */
    public String getSummary() {
        return String.format("%.2f %s %.2f = %.2f", x, operation, y, result);
    }

    @Override
    public String toString() {
        return getSummary();
    }
}
