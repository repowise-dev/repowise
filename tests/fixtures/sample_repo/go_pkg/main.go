// Package main is the entry point for the sample Go calculator service.
// It wires together the calculator and types packages and exposes a simple
// command-line interface.
package main

import (
	"fmt"
	"os"

	"github.com/repowise-ai/sample/calculator"
	"github.com/repowise-ai/sample/types"
)

func main() {
	calc := calculator.New()
	result, err := calc.Add(types.Operands{X: 10, Y: 5})
	if err != nil {
		fmt.Fprintf(os.Stderr, "error: %v\n", err)
		os.Exit(1)
	}
	fmt.Printf("Result: %.2f\n", result)
}

// RunOperation executes a named arithmetic operation.
func RunOperation(name string, x, y float64) (float64, error) {
	calc := calculator.New()
	switch name {
	case "add":
		return calc.Add(types.Operands{X: x, Y: y})
	case "subtract":
		return calc.Subtract(types.Operands{X: x, Y: y})
	case "multiply":
		return calc.Multiply(types.Operands{X: x, Y: y})
	case "divide":
		return calc.Divide(types.Operands{X: x, Y: y})
	default:
		return 0, fmt.Errorf("unknown operation: %s", name)
	}
}
