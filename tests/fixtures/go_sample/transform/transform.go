// Package transform provides string transformation functions.
package transform

import "strings"

// Upper returns s converted to upper case.
// Passed as a function value to Apply — never called directly.
func Upper(s string) string {
	return strings.ToUpper(s)
}

// Lower returns s converted to lower case.
// Passed as a function value to Apply — never called directly.
func Lower(s string) string {
	return strings.ToLower(s)
}
