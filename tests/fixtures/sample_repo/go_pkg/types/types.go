// Package types defines the shared domain types used across the sample service.
package types

// Operation represents a supported arithmetic operation.
type Operation string

const (
	OpAdd      Operation = "add"
	OpSubtract Operation = "subtract"
	OpMultiply Operation = "multiply"
	OpDivide   Operation = "divide"
)

// Operands holds the two numeric values for a binary arithmetic operation.
type Operands struct {
	X float64
	Y float64
}

// CalculationRecord is a single entry in a Calculator's history.
type CalculationRecord struct {
	Operation Operation
	Operands  Operands
	Result    float64
	Summary   string
}

// Arithmetic defines the interface that any calculator implementation must satisfy.
type Arithmetic interface {
	Add(ops Operands) (float64, error)
	Subtract(ops Operands) (float64, error)
	Multiply(ops Operands) (float64, error)
	Divide(ops Operands) (float64, error)
}
