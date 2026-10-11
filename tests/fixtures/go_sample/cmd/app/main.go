// Command app is the entry point that reaches the store and service packages.
package main

import (
	"example.com/gosample/service"
	"example.com/gosample/store"
	"example.com/gosample/transform"
)

// run wires the concrete store into a service. Naming *store.MemStore (a
// parameter type) and *service.Service (a return type) exercises the type_use
// resolution that keeps constructor-returned concrete types reachable.
func run(st *store.MemStore) *service.Service {
	return service.New(st)
}

// apply accepts a transform function as a value — exercises the pkg.Func
// argument-position capture that rescues functions passed as arguments.
func apply(fn func(string) string) {}

func main() {
	run(store.NewMemStore()).Run()
	apply(transform.Upper)
	apply(transform.Lower)
}
