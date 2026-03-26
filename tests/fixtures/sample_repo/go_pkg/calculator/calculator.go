// Package calculator provides stateful arithmetic operations with history.
package calculator

import (
	"errors"
	"fmt"

	"github.com/repowise-ai/sample/types"
)

// ErrDivisionByZero is returned when a division-by-zero is attempted.
var ErrDivisionByZero = errors.New("division by zero")

// Calculator performs arithmetic operations and records their history.
type Calculator struct {
	history []types.CalculationRecord
}

// New returns a new, empty Calculator.
func New() *Calculator {
	return &Calculator{}
}

// Add returns the sum of the operands and appends the result to history.
func (c *Calculator) Add(ops types.Operands) (float64, error) {
	result := ops.X + ops.Y
	c.record(types.OpAdd, ops, result)
	return result, nil
}

// Subtract returns ops.X - ops.Y and appends the result to history.
func (c *Calculator) Subtract(ops types.Operands) (float64, error) {
	result := ops.X - ops.Y
	c.record(types.OpSubtract, ops, result)
	return result, nil
}

// Multiply returns the product of the operands.
func (c *Calculator) Multiply(ops types.Operands) (float64, error) {
	result := ops.X * ops.Y
	c.record(types.OpMultiply, ops, result)
	return result, nil
}

// Divide returns ops.X / ops.Y. Returns ErrDivisionByZero if ops.Y == 0.
func (c *Calculator) Divide(ops types.Operands) (float64, error) {
	if ops.Y == 0 {
		return 0, ErrDivisionByZero
	}
	result := ops.X / ops.Y
	c.record(types.OpDivide, ops, result)
	return result, nil
}

// History returns a copy of all calculation records.
func (c *Calculator) History() []types.CalculationRecord {
	out := make([]types.CalculationRecord, len(c.history))
	copy(out, c.history)
	return out
}

// ClearHistory removes all recorded calculations.
func (c *Calculator) ClearHistory() {
	c.history = c.history[:0]
}

// record appends a new entry to the internal history slice.
func (c *Calculator) record(op types.Operation, ops types.Operands, result float64) {
	c.history = append(c.history, types.CalculationRecord{
		Operation: op,
		Operands:  ops,
		Result:    result,
		Summary:   fmt.Sprintf("%.2f %s %.2f = %.2f", ops.X, op, ops.Y, result),
	})
}
