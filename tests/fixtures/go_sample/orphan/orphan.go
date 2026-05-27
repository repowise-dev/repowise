// Package orphan is never imported by any other package in the module — it
// is genuinely dead and its file must be flagged unreachable.
package orphan

// Lonely is exported but unreachable: nothing imports this package.
func Lonely() {}
