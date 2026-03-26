package com.repowise.sample;

/**
 * Enum representing the four supported arithmetic operations.
 */
public enum Operation {
    ADD("add"),
    SUBTRACT("subtract"),
    MULTIPLY("multiply"),
    DIVIDE("divide");

    private final String label;

    Operation(String label) {
        this.label = label;
    }

    /** Returns the lowercase label used in summaries. */
    public String getLabel() {
        return label;
    }

    @Override
    public String toString() {
        return label;
    }
}
