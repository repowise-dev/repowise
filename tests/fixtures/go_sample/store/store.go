// Package store defines the persistence contract.
package store

// Store is the persistence interface. It is implemented by MemStore in the
// sibling file memory.go — there is no `implements` keyword, so only the
// structural interface-satisfaction pass connects them.
type Store interface {
	Get(key string) string
	Put(key, value string)
}

// Config is consumed only as a field type by service.Service. It is never
// imported by name nor built via a composite literal, so only the type_use
// rescue keeps it from reading as a dead export.
type Config struct {
	Namespace string
}
